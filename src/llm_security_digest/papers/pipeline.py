from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib import parse

from .content import extract_html, extract_pdf, persist_content
from .http import HttpClient
from .models import FACT_FIELDS, PaperFacts, SearchPlan, SelectionEntry, normalize_doi, normalize_title, utc_now
from .sources import (
    ArxivSource,
    CrossrefSource,
    GoogleScholarEnricher,
    IeeeXploreSource,
    OfficialSource,
    OpenReviewSource,
    official_route_for_paper,
    formal_duplicate_match,
    reconcile_arxiv_to_formal,
    trusted_fulltext_url,
    trusted_fulltext_hosts,
)


def default_client() -> HttpClient:
    email = os.getenv("LLMSD_CONTACT_EMAIL", "").strip()
    suffix = f" ({email})" if email else ""
    fallback = None
    if os.getenv("LLMSD_HEADLESS_FALLBACK", "").strip().casefold() in {"1", "true", "yes", "on"}:
        # Keep Playwright optional and lazy.  Direct urllib remains the first
        # transport; the browser is consulted only for blocked registered
        # hosts, and OpenReview continues through its official client.
        from .headless import HeadlessResponseTransport

        fallback = HeadlessResponseTransport()
    return HttpClient(user_agent=f"LLMSecurityDigest/2.0{suffix}", fallback_transport=fallback)


def _error_message(exc: Exception, *, limit: int = 400) -> str:
    """Keep credentials out of manifests even when a dependency leaks a URL."""
    message = str(exc)
    for secret_name in ("SERPAPI_API_KEY",):
        secret = os.getenv(secret_name, "")
        if secret:
            message = message.replace(secret, "<redacted>")
    message = re.sub(
        r"([?&](?:api[_-]?key|access[_-]?token|token|secret|password)=)[^&\s]+",
        r"\1<redacted>",
        message,
        flags=re.IGNORECASE,
    )
    return message[:limit]


def _matches_keywords(paper: PaperFacts, keywords: list[str]) -> bool:
    if not keywords:
        return True
    haystack = f"{paper.title}\n{paper.abstract}".casefold()
    return any(keyword.casefold() in haystack for keyword in keywords)


def _matches_date_window(paper: PaperFacts, plan: SearchPlan) -> bool:
    published = (paper.published_at or "")[:10]
    if not published:
        # Proceedings records sometimes provide only the conference year.
        # The adapter has already bounded discovery to that year, but must not
        # invent a day merely to satisfy a date filter. Keep formal records in
        # scope; un-dated arXiv records are not eligible for a date window.
        return paper.collection_tier == "formal"
    if plan.date_from and published < plan.date_from:
        return False
    if plan.date_to and published > plan.date_to:
        return False
    return True


def _same_discovered_facts(left: PaperFacts, right: PaperFacts) -> bool:
    """Compare duplicate hits without treating response provenance as a fact.

    A paper can be returned by several arXiv queries. Each query has a
    different response hash and URL, but the extracted metadata is expected to
    be identical. A real metadata disagreement must still be reported.
    """
    left_value = left.to_dict()
    right_value = right.to_dict()
    left_value.pop("provenance", None)
    right_value.pop("provenance", None)
    return left_value == right_value


def _source_summary_status(reports: list[dict[str, Any]], *, discovered: int, incomplete: int) -> str:
    """Aggregate adapter statuses without hiding a failed source stage."""
    statuses = {str(report.get("status", "")).casefold() for report in reports}
    if "error" in statuses:
        return "partial" if discovered or incomplete else "error"
    if "partial" in statuses:
        return "partial"
    if "skipped" in statuses and statuses <= {"skipped"}:
        return "skipped"
    return "ok"


def _deduplicate_formal_records(records: list[PaperFacts], reports: list[dict[str, Any]]) -> list[PaperFacts]:
    """Keep the first source-ordered formal record only when identity is proven."""
    canonical: list[PaperFacts] = []
    for paper in records:
        duplicate = next(
            ((existing, proof) for existing in canonical if (proof := formal_duplicate_match(existing, paper))),
            None,
        )
        if duplicate is None:
            canonical.append(paper)
            continue
        existing, (method, similarity) = duplicate
        report: dict[str, Any] = {
            "source": "formal_dedup",
            "adapter": "formal_dedup",
            "status": "duplicate",
            "canonical_id": existing.paper_id,
            "duplicate_id": paper.paper_id,
            "method": method,
        }
        if similarity is not None:
            report["author_jaccard"] = similarity
        reports.append(report)
    return canonical


def collect(plan: SearchPlan, *, client: HttpClient | None = None) -> dict[str, Any]:
    client = client or default_client()
    adapters = {
        "arxiv": ArxivSource(client),
        "official": OfficialSource(client),
        # The official client remains primary. The broker is available only
        # for its bounded v2 challenge-recovery route.
        "openreview": OpenReviewSource(http_client=client),
        "crossref": CrossrefSource(client, contact_email=os.getenv("LLMSD_CONTACT_EMAIL")),
        "ieee_xplore": IeeeXploreSource(client),
    }
    candidates: dict[str, PaperFacts] = {}
    reports: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    # Official records are fetched before arXiv so reconciliation always has
    # a canonical pool available. The plan order remains visible in reports.
    source_order = sorted(plan.sources, key=lambda name: {"official": 0, "openreview": 1, "crossref": 1, "ieee_xplore": 2, "arxiv": 3}.get(name, 4))
    for source_name in source_order:
        try:
            source = adapters[source_name]
            result = source.discover_result(plan) if hasattr(source, "discover_result") else None
            discovered = result.papers if result is not None else source.discover(plan)
            if result is not None:
                incomplete.extend(result.incomplete)
                reports.extend(result.reports)
            accepted = 0
            for paper in discovered:
                if not _matches_keywords(paper, plan.filter_keywords) or not _matches_date_window(paper, plan):
                    continue
                existing = candidates.get(paper.paper_id)
                if existing and not _same_discovered_facts(existing, paper):
                    reports.append({"source": source_name, "status": "conflict", "paper_id": paper.paper_id})
                    continue
                # The source parser owns the formal/preprint boundary.  An
                # arXiv candidate can carry arbitrary JSON metadata, so its
                # tier and match state must be reset before reconciliation.
                if paper.source == "arxiv":
                    paper.collection_tier = "arxiv_fallback"
                    paper.match_state = "unmatched"
                elif paper.collection_tier == "unknown":
                    paper.collection_tier = "arxiv_fallback" if paper.source == "arxiv" else "formal"
                if paper.match_state == "unresolved":
                    paper.match_state = "unmatched" if paper.source == "arxiv" else "canonical"
                candidates[paper.paper_id] = paper
                accepted += 1
            if result is None:
                reports.append({"source": source_name, "adapter": source_name, "status": "ok", "discovered": len(discovered), "accepted": accepted})
            else:
                source_reports = [
                    report for report in reports
                    if report.get("source") == source_name and report.get("adapter") != "summary"
                ]
                reports.append({
                    "source": source_name,
                    "adapter": "summary",
                    "status": _source_summary_status(
                        source_reports,
                        discovered=len(discovered),
                        incomplete=len(result.incomplete),
                    ),
                    "discovered": len(discovered),
                    "accepted": accepted,
                    "incomplete": len(result.incomplete),
                })
        except Exception as exc:
            reports.append({"source": source_name, "adapter": source_name, "status": "error", "error_type": type(exc).__name__, "message": _error_message(exc, limit=300)})
    formal = _deduplicate_formal_records(
        [paper for paper in candidates.values() if paper.source != "arxiv" and paper.collection_tier == "formal"],
        reports,
    )
    arxiv = [paper for paper in candidates.values() if paper.source == "arxiv"]
    # An arXiv record is retained as an alternate identity only when the
    # formal source match is unambiguous. Unresolved matches remain visible in
    # the candidate artifact but cannot pass materialization.
    reconciled: dict[str, PaperFacts] = {paper.paper_id: paper for paper in formal}
    for preprint in arxiv:
        canonical, evidence = reconcile_arxiv_to_formal(preprint, formal)
        if canonical is not None:
            reconciled[canonical.paper_id] = canonical
            continue
        if evidence.get("state") == "ambiguous":
            preprint.match_state = "unresolved"
            preprint.unresolved_evidence.append(evidence)
        elif preprint.match_state == "unresolved":
            preprint.match_state = "unmatched"
        reconciled[preprint.paper_id] = preprint
    # Keep authoritative records ahead of preprint fallbacks, and preserve
    # the established newest-first queue order within each tier.
    formal_order = sorted(
        (paper for paper in reconciled.values() if paper.collection_tier == "formal"),
        key=lambda paper: (paper.published_at or "", paper.paper_id),
        reverse=True,
    )
    fallback_order = sorted(
        (paper for paper in reconciled.values() if paper.collection_tier != "formal"),
        key=lambda paper: (paper.published_at or "", paper.paper_id),
        reverse=True,
    )
    ordered = [*formal_order, *fallback_order]
    return {
        "schema_version": 2,
        "generated_at": utc_now(),
        "plan": asdict(plan),
        "source_reports": reports,
        "incomplete": incomplete,
        "candidate_queue": {"incomplete": incomplete},
        "total": len(ordered),
        "candidates": [paper.to_dict() for paper in ordered],
        "candidate_metadata": {
            "formal": sum(paper.collection_tier == "formal" for paper in ordered),
            "arxiv_fallback": sum(paper.collection_tier == "arxiv_fallback" for paper in ordered),
            "unresolved": sum(bool(paper.unresolved_evidence) for paper in ordered),
        },
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _extract_bibtex_field(bibtex: str, field: str) -> str:
    match = re.search(rf"(?im)(?:^|,)\s*{re.escape(field)}\s*=\s*", bibtex)
    if not match:
        return ""
    index = match.end()
    while index < len(bibtex) and bibtex[index].isspace():
        index += 1
    if index >= len(bibtex):
        return ""
    opener = bibtex[index]
    if opener == "{":
        depth = 1
        result: list[str] = []
        index += 1
        while index < len(bibtex) and depth:
            char = bibtex[index]
            if char == "{" and (index == 0 or bibtex[index - 1] != "\\"):
                depth += 1
                if depth > 1:
                    result.append(char)
            elif char == "}" and (index == 0 or bibtex[index - 1] != "\\"):
                depth -= 1
                if depth:
                    result.append(char)
            else:
                result.append(char)
            index += 1
        return " ".join("".join(result).split())
    if opener == '"':
        end = index + 1
        while end < len(bibtex):
            if bibtex[end] == '"' and bibtex[end - 1] != "\\":
                return " ".join(bibtex[index + 1:end].split())
            end += 1
    return ""


def validate_bibtex(paper: PaperFacts, bibtex: str) -> None:
    if not bibtex.lstrip().startswith("@"):
        raise ValueError("citation endpoint did not return BibTeX")
    bib_title = _extract_bibtex_field(bibtex, "title")
    if not bib_title or normalize_title(bib_title) != normalize_title(paper.title):
        raise ValueError("BibTeX title does not match authoritative metadata")
    bib_authors = _extract_bibtex_field(bibtex, "author")
    bib_author_names = _split_bibtex_authors(bib_authors)
    if not bib_author_names or not _authors_match(paper.authors, bib_author_names):
        raise ValueError("BibTeX authors do not match authoritative metadata")
    if paper.doi:
        bib_doi = normalize_doi(_extract_bibtex_field(bibtex, "doi"))
        if not bib_doi:
            raise ValueError("BibTeX DOI is missing for authoritative DOI record")
        if bib_doi != normalize_doi(paper.doi):
            raise ValueError("BibTeX DOI does not match authoritative metadata")
    if paper.source == "arxiv":
        eprint = _extract_bibtex_field(bibtex, "eprint")
        url = _extract_bibtex_field(bibtex, "url")
        if not eprint:
            parsed_url = parse.urlsplit(url)
            path_match = re.fullmatch(
                r"/(?:abs|pdf)/((?:\d{4}\.\d{4,5}|[a-z][a-z-]+/\d{7})(?:v\d+)?)(?:\.pdf)?/?",
                parsed_url.path,
                flags=re.IGNORECASE,
            )
            if (
                parsed_url.scheme != "https"
                or (parsed_url.hostname or "").casefold().rstrip(".") != "arxiv.org"
                or not path_match
                or re.sub(r"v\d+$", "", path_match.group(1), flags=re.IGNORECASE) != paper.source_id
            ):
                raise ValueError("BibTeX does not identify the arXiv paper")
        if eprint and re.sub(r"v\d+$", "", eprint) != paper.source_id:
            raise ValueError("BibTeX arXiv id does not match authoritative metadata")


def _split_bibtex_authors(value: str) -> list[str]:
    """Split BibTeX's ``and``-separated author list without breaking braces."""
    values: list[str] = []
    start = 0
    depth = 0
    index = 0
    lowered = value.casefold()
    while index < len(value):
        char = value[index]
        if char == "{":
            depth += 1
        elif char == "}" and depth:
            depth -= 1
        if depth == 0 and lowered.startswith("and", index):
            before = index == 0 or value[index - 1].isspace()
            after = index + 3 == len(value) or value[index + 3].isspace()
            if before and after:
                item = value[start:index].strip()
                if item:
                    values.append(item)
                index += 3
                start = index
                continue
        index += 1
    item = value[start:].strip()
    if item:
        values.append(item)
    return values


def _author_family(value: str) -> str:
    value = value.strip().strip("{}").strip()
    if "," in value:
        value = value.split(",", 1)[0]
    else:
        value = value.split()[-1] if value.split() else ""
    return normalize_title(value)


def _author_full_key(value: str) -> str:
    """Normalize ``Given Family`` and BibTeX ``Family, Given`` forms."""
    value = value.strip().strip("{}").strip()
    if "," in value:
        family, given = value.split(",", 1)
        value = f"{given} {family}"
    return normalize_title(value)


def _authors_match(source_authors: list[str], bib_authors: list[str]) -> bool:
    if len(source_authors) != len(bib_authors):
        return False
    source_full = sorted(_author_full_key(author) for author in source_authors)
    bib_full = sorted(_author_full_key(author) for author in bib_authors)
    if source_full and source_full == bib_full and all(source_full):
        return True
    source_by_family: dict[str, list[str]] = {}
    bib_by_family: dict[str, list[str]] = {}
    for author in source_authors:
        source_by_family.setdefault(_author_family(author), []).append(author)
    for author in bib_authors:
        bib_by_family.setdefault(_author_family(author), []).append(author)
    if not source_by_family or set(source_by_family) != set(bib_by_family):
        return False
    for family, source_values in source_by_family.items():
        bib_values = bib_by_family[family]
        if not family or len(source_values) != len(bib_values):
            return False
        source_initials = sorted(_author_initials(author) for author in source_values)
        bib_initials = sorted(_author_initials(author) for author in bib_values)
        if not all(source_initials) or not all(bib_initials) or source_initials != bib_initials:
            return False
    return True


def _author_initials(value: str) -> str:
    value = value.strip().strip("{}").strip()
    if "," in value:
        _family, given = value.split(",", 1)
    else:
        parts = value.split()
        given = " ".join(parts[:-1])
    return "".join(part[0].casefold() for part in re.findall(r"[A-Za-z0-9]+", given) if part)


def _bibtex_entries(value: str) -> list[str]:
    """Split a bibliography without treating nested braces as new records."""

    entries: list[str] = []
    cursor = 0
    while True:
        start = value.find("@", cursor)
        if start < 0:
            break
        opener = value.find("{", start)
        if opener < 0:
            break
        depth = 0
        index = opener
        while index < len(value):
            char = value[index]
            if char == "{" and (index == 0 or value[index - 1] != "\\"):
                depth += 1
            elif char == "}" and (index == 0 or value[index - 1] != "\\"):
                depth -= 1
                if depth == 0:
                    entries.append(value[start:index + 1].strip())
                    cursor = index + 1
                    break
            index += 1
        else:
            break
    return entries


def _bibtex_source_url(paper: PaperFacts) -> str | None:
    source = str(paper.source or "").casefold()
    source_id = str(paper.source_id or "").strip()
    if source in {"acl", "emnlp"} and re.fullmatch(r"(?:19|20)\d{2}\.[a-z0-9-]+\.[1-9]\d*", source_id, re.IGNORECASE):
        return f"https://aclanthology.org/{source_id}.bib"
    if paper.source == "pmlr":
        parts = source_id.rsplit(":", 1)
        if len(parts) == 2 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", parts[0]) and re.fullmatch(r"v\d+", parts[1], flags=re.IGNORECASE):
            return f"https://proceedings.mlr.press/{parts[1]}/assets/bib/bibliography.bib"
    if source == "ijcai":
        match = re.fullmatch(r"((?:19|20)\d{2})-([1-9]\d{0,4})", source_id)
        if match:
            return f"https://www.ijcai.org/proceedings/{match.group(1)}/bibtex/{int(match.group(2))}"
    metadata = paper.source_metadata if isinstance(paper.source_metadata, dict) else {}
    value = metadata.get("bibtex_url") if getattr(paper, "_authoritative_refresh", False) else None
    route_id = source_id.split(":", 1)[-1] if source in {"neurips", "usenix", "ndss", "cvpr"} else source_id
    trusted_hosts = {
        "neurips": {"proceedings.neurips.cc"},
        "usenix": {"www.usenix.org"},
        "ndss": {"www.ndss-symposium.org"},
        "aaai_ojs": {"ojs.aaai.org"},
        "cvpr": {"openaccess.thecvf.com"},
        "eccv": {"www.ecva.net"},
    }.get(source, set())
    if isinstance(value, str) and value.startswith("https://") and trusted_hosts:
        parsed = parse.urlsplit(value)
        is_neurips_export = (
            source == "neurips"
            and parsed.hostname
            and parsed.hostname.casefold().rstrip(".") == "proceedings.neurips.cc"
            and parsed.query == ""
            and parsed.username is None
            and parsed.password is None
            and re.fullmatch(r"/paper_files/paper/[1-9]\d*-/bibtex", parsed.path)
        )
        if is_neurips_export or (
            parsed.hostname
            and parsed.hostname.casefold().rstrip(".") in trusted_hosts
            and route_id.casefold() in parsed.path.casefold()
            and parsed.username is None
            and parsed.password is None
        ):
            return value
    return None


def _bibtex_hosts(paper: PaperFacts) -> frozenset[str] | None:
    if paper.doi:
        # DOI content negotiation redirects to the publisher.  The DOI is
        # already authoritative and strictly validated; the resolver's
        # publisher redirect is the one intentionally open route.
        return None
    return {
        "arxiv": frozenset({"arxiv.org"}),
        "openreview": frozenset({"openreview.net"}),
        "acl": frozenset({"aclanthology.org"}),
        "emnlp": frozenset({"aclanthology.org"}),
        "pmlr": frozenset({"proceedings.mlr.press"}),
        "neurips": frozenset({"proceedings.neurips.cc"}),
        "ijcai": frozenset({"www.ijcai.org"}),
        "usenix": frozenset({"www.usenix.org"}),
        "ndss": frozenset({"www.ndss-symposium.org"}),
        "aaai_ojs": frozenset({"ojs.aaai.org"}),
        "cvpr": frozenset({"openaccess.thecvf.com"}),
        "eccv": frozenset({"www.ecva.net"}),
    }.get(str(paper.source or "").casefold())


def _validate_bibtex_entry(paper: PaperFacts, text: str) -> str | None:
    for entry in _bibtex_entries(text):
        try:
            validate_bibtex(paper, entry)
        except ValueError:
            continue
        return entry
    return None


def fetch_bibtex(paper: PaperFacts, *, client: HttpClient) -> tuple[str, str, dict[str, Any]]:
    metadata = paper.source_metadata if isinstance(paper.source_metadata, dict) else {}
    inline = metadata.get("bibtex_inline") if getattr(paper, "_authoritative_refresh", False) else None
    if isinstance(inline, str) and inline.lstrip().startswith("@"):
        validate_bibtex(paper, inline)
        return inline.strip(), paper.landing_url, {
            "source": "official_detail_bibtex",
            "source_url": paper.landing_url,
            "fetched_at": utc_now(),
            "response_sha256": (paper.provenance.get("title", {}) if isinstance(paper.provenance, dict) else {}).get("response_sha256"),
            "extractor_version": "inline-1",
        }

    # DOI content negotiation is the canonical path for formal records (and
    # for arXiv records that advertise a DOI). It avoids treating Crossref's
    # transform endpoint as a second, potentially divergent authority.
    if paper.doi:
        url = f"https://doi.org/{parse.quote(normalize_doi(paper.doi), safe='')}"
    elif paper.source == "arxiv":
        url = f"https://arxiv.org/bibtex/{parse.quote(paper.source_id)}"
    elif paper.source == "openreview":
        url = f"https://openreview.net/bibtex?id={parse.quote(paper.source_id)}"
    else:
        url = _bibtex_source_url(paper)
    if not url:
        raise ValueError("no authoritative BibTeX endpoint for paper")
    response = client.get(
        url,
        headers={"Accept": "application/x-bibtex"},
        min_interval=0.25,
        max_bytes=20 * 1024 * 1024 if paper.source == "pmlr" else 2 * 1024 * 1024,
        # DOI content negotiation is intentionally handled by the resolver;
        # all non-DOI routes are fixed baseline hosts.  The refreshed record,
        # rather than the candidate JSON, determines this URL.
        allowed_hosts=_bibtex_hosts(paper),
    )
    raw_bibtex = response.text().strip()
    bibtex = _validate_bibtex_entry(paper, raw_bibtex) if paper.source == "pmlr" else raw_bibtex
    if not bibtex:
        raise ValueError("official BibTeX response has no title/author-matching entry")
    validate_bibtex(paper, bibtex)
    provenance = {
        "source": "authoritative_bibtex_endpoint",
        "source_url": url,
        "requested_url": url,
        "final_url": response.final_url,
        "transport": response.transport,
        "redirect_chain": list(response.redirect_chain),
        "fetched_at": utc_now(),
        "response_sha256": response.sha256,
        "extractor_version": "1",
    }
    return bibtex, url, provenance


def fetch_fulltext(paper: PaperFacts, *, client: HttpClient, data_dir: Path, max_bytes: int) -> dict[str, Any]:
    candidates: list[tuple[str, str]] = []
    allowed_hosts = trusted_fulltext_hosts(paper)
    if paper.source == "arxiv":
        candidates.append((f"https://arxiv.org/html/{parse.quote(paper.source_id)}", "html"))
        # The PDF URL is derived from the verified arXiv id rather than a
        # candidate-provided link.  Other sources use their refreshed,
        # authoritative PDF URL but still pass the host policy below.
        candidates.append((f"https://arxiv.org/pdf/{parse.quote(paper.source_id)}", "pdf"))
    else:
        candidates.append((paper.pdf_url, "pdf"))
    failures: list[str] = []
    for url, kind in candidates:
        try:
            if not trusted_fulltext_url(paper, url):
                raise ValueError("full-text URL does not match the registered source path")
            response = client.get(
                url,
                min_interval=0.25,
                max_bytes=max_bytes,
                allowed_hosts=allowed_hosts,
            )
            if kind == "html":
                extracted, sections = extract_html(response.body, paper.title)
            else:
                extracted, sections = extract_pdf(response.body, paper.title)
            return persist_content(
                data_dir=data_dir,
                paper_id=paper.paper_id,
                body=response.body,
                extracted_text=extracted,
                sections=sections,
                extension=kind,
                source_url=url,
            )
        except Exception as exc:
            failures.append(f"{kind}:{type(exc).__name__}:{str(exc)[:160]}")
    raise ValueError("; ".join(failures) or "no full-text candidate")


_CANDIDATE_SOURCES = frozenset({
    "arxiv", "openreview", "crossref", "ieee_xplore",
    "acl", "emnlp", "pmlr", "neurips", "aaai_ojs", "ijcai", "usenix", "ndss",
    "cvpr", "eccv",
})


def _validate_candidate_identity(paper: PaperFacts) -> None:
    """Validate the only candidate fields used to select an authority."""
    if paper.source not in _CANDIDATE_SOURCES:
        raise ValueError(f"unsupported candidate source: {paper.source!r}")
    if paper.paper_id != f"{paper.source}:{paper.source_id}":
        if not (paper.source in {"crossref", "ieee_xplore"} and paper.paper_id == f"doi:{normalize_doi(paper.source_id)}"):
            raise ValueError("candidate paper_id does not match source/source_id")
    source_id = str(paper.source_id or "").strip()
    if paper.source == "arxiv":
        if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z][a-z-]+/\d{7})(?:v\d+)?", source_id, re.IGNORECASE):
            raise ValueError("invalid arXiv source id")
    elif paper.source == "openreview":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._~:/-]{0,240}", source_id):
            raise ValueError("invalid OpenReview forum id")
    elif paper.source in {"crossref", "ieee_xplore"}:
        if not re.fullmatch(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", normalize_doi(source_id), re.IGNORECASE):
            raise ValueError("invalid DOI source id")
        if paper.paper_id != f"doi:{normalize_doi(source_id)}":
            raise ValueError("DOI paper id does not match source id")
        if normalize_doi(paper.doi or "") != normalize_doi(source_id):
            raise ValueError("candidate DOI does not match source id")
    else:
        # The official adapter performs the detailed source-id grammar check
        # and canonical URL construction before any network request.
        official_route_for_paper(paper)


def refresh_authoritative(paper: PaperFacts, *, client: HttpClient) -> PaperFacts:
    """Re-fetch metadata for a selected ID so candidates cannot carry facts into output."""
    _validate_candidate_identity(paper)
    if paper.source == "arxiv":
        refreshed = ArxivSource(client).fetch_by_id(paper.source_id)
    elif paper.source == "openreview":
        refreshed = OpenReviewSource(http_client=client).fetch_by_id(paper.source_id)
    elif paper.source == "crossref" and paper.doi:
        refreshed = CrossrefSource(client).fetch_by_doi(paper.doi)
    elif paper.source == "ieee_xplore" and paper.doi:
        refreshed = IeeeXploreSource(client).fetch_by_doi(paper.doi)
    elif paper.source in {"acl", "emnlp", "pmlr", "neurips", "aaai_ojs", "ijcai", "usenix", "ndss", "cvpr", "eccv"}:
        # OfficialSource derives the detail URL from the baseline source/id
        # grammar. Candidate venue metadata and landing URLs are not routing
        # inputs.
        refreshed = OfficialSource(client).fetch_by_id(paper)
    else:
        raise ValueError(f"no authoritative identity refresh for source {paper.source!r}")
    if refreshed.paper_id != paper.paper_id:
        raise ValueError(
            f"authoritative identity mismatch: expected {paper.paper_id}, got {refreshed.paper_id}"
        )
    if normalize_title(refreshed.title) != normalize_title(paper.title) or not _authors_match(paper.authors, refreshed.authors):
        raise ValueError("authoritative refresh metadata does not match the discovered record")
    if paper.doi and normalize_doi(refreshed.doi or "") != normalize_doi(paper.doi):
        raise ValueError("authoritative refresh DOI does not match the discovered record")
    # Dynamic marker is intentionally excluded from PaperFacts.to_dict(); it
    # only lets the citation layer distinguish a refreshed source record from
    # an untrusted candidate object in this process.
    setattr(refreshed, "_authoritative_refresh", True)
    return refreshed


def materialize(
    *,
    candidates_payload: dict[str, Any],
    selections: list[SelectionEntry],
    data_dir: Path,
    target: int = 10,
    scholar_limit: int = 30,
    client: HttpClient | None = None,
    max_pdf_bytes: int = 25 * 1024 * 1024,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(target) is not int or not 1 <= target <= 10:
        raise ValueError("target must be between 1 and 10")
    if type(scholar_limit) is not int or not 0 <= scholar_limit <= 100:
        raise ValueError("scholar_limit must be between 0 and 100")
    client = client or default_client()
    raw_candidates = candidates_payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("candidates payload must contain a candidates array")
    raw_plan = candidates_payload.get("plan")
    if raw_plan is not None and not isinstance(raw_plan, dict):
        raise ValueError("candidates payload plan must be an object")
    core_keywords = [] if raw_plan is None else raw_plan.get("core_keywords", [])
    if (
        not isinstance(core_keywords, list)
        or len(core_keywords) > 100
        or any(not isinstance(value, str) or not value.strip() or len(value) > 100 for value in core_keywords)
    ):
        raise ValueError("candidates payload core_keywords is invalid")
    candidates: dict[str, PaperFacts] = {}
    for index, item in enumerate(raw_candidates):
        if not isinstance(item, dict) or not str(item.get("paper_id", "")).strip():
            raise ValueError(f"candidate {index} is missing paper_id")
        paper = PaperFacts.from_dict(item)
        if paper.paper_id in candidates:
            raise ValueError(f"candidate {index} has duplicate paper_id: {paper.paper_id}")
        candidates[paper.paper_id] = paper
    scholar = GoogleScholarEnricher(client)
    verified: list[PaperFacts] = []
    decisions: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    scholar_calls = 0
    track_counts = {"core": 0, "broad": 0}
    # Preserve Hermes ranking within each tier, but always spend the first
    # verification attempts on formal/canonical records. Fallback preprints
    # are considered only after formal attempts have produced a shortfall.
    formal_selections: list[SelectionEntry] = []
    fallback_selections: list[SelectionEntry] = []
    for selection in selections:
        candidate = candidates.get(selection.paper_id)
        if candidate is not None and candidate.source == "arxiv":
            fallback_selections.append(selection)
        else:
            formal_selections.append(selection)
    for selection in [*formal_selections, *fallback_selections]:
        if len(verified) >= target:
            break
        if type(selection.track) is not str or selection.track not in track_counts:
            rejected.append({"paper_id": selection.paper_id, "reason": "invalid_track"})
            continue
        paper = candidates.get(selection.paper_id)
        if paper is None:
            rejected.append({"paper_id": selection.paper_id, "reason": "unknown_paper_id"})
            continue
        if paper.unresolved_evidence:
            rejected.append({
                "paper_id": paper.paper_id,
                "reason": "unresolved_evidence",
                "evidence": paper.unresolved_evidence,
            })
            continue
        if track_counts[selection.track] >= 5:
            rejected.append({
                "paper_id": paper.paper_id,
                "reason": "track_quota_exceeded",
                "track": selection.track,
                "limit": 5,
            })
            continue
        try:
            _validate_candidate_identity(paper)
            paper.validate_discovered()
            paper = refresh_authoritative(paper, client=client)
            # The refreshed record owns facts, provenance, routing metadata,
            # and tier/match state. Candidate metadata is never merged into
            # the object that becomes facts.json.
            paper.validate_discovered()
            if selection.track == "core" and core_keywords and not _matches_keywords(paper, core_keywords):
                rejected.append({
                    "paper_id": paper.paper_id,
                    "reason": "core_keyword_mismatch",
                })
                continue
            paper.bibtex, paper.bibtex_url, bib_provenance = fetch_bibtex(paper, client=client)
            paper.provenance["bibtex"] = bib_provenance
            paper.content = fetch_fulltext(paper, client=client, data_dir=data_dir, max_bytes=max_pdf_bytes)
            # Scholar is optional enrichment and must never be allowed to
            # rescue an otherwise unverifiable record or consume quota first.
            paper.validate_materialized()
            if scholar_calls < scholar_limit:
                try:
                    paper.scholar = scholar.enrich(paper)
                except Exception as exc:
                    paper.scholar = {
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "message": _error_message(exc, limit=200),
                    }
                scholar_calls += 1
            verified.append(paper)
            track_counts[selection.track] += 1
            decisions[paper.paper_id] = asdict(selection)
        except Exception as exc:
            rejected.append({
                "paper_id": paper.paper_id,
                "reason": "verification_failed",
                "error_type": type(exc).__name__,
                "message": _error_message(exc),
            })
    facts = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "target": target,
        "total": len(verified),
        "papers": [paper.to_dict() for paper in verified],
    }
    manifest = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "status": "ok" if verified else "failed",
        "target": target,
        "published": len(verified),
        "shortfall": max(target - len(verified), 0),
        "selection_decisions": decisions,
        "rejected": rejected,
        "source_reports": candidates_payload.get("source_reports", []),
        "scholar": {"configured": scholar.available, "calls": scholar_calls},
        "collection": {
            "formal": sum(paper.source != "arxiv" or paper.collection_tier == "formal" for paper in verified),
            "arxiv_fallback": sum(paper.source == "arxiv" and paper.match_state == "unmatched" for paper in verified),
            "unresolved_rejected": sum(item.get("reason") == "unresolved_evidence" for item in rejected),
            "track_counts": dict(track_counts),
            "track_limits": {"core": 5, "broad": 5},
        },
    }
    return facts, manifest


def load_analysis(path: Path, valid_ids: set[str]) -> dict[str, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    values = raw.get("papers") if isinstance(raw, dict) else None
    if not isinstance(values, list):
        raise ValueError("analysis must contain a papers array")
    result: dict[str, dict[str, Any]] = {}
    allowed = {"paper_id", "category", "summary_zh", "problem_zh", "method_zh", "result_zh", "contribution_zh"}
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"analysis {index} must be an object")
        forbidden = set(value) & FACT_FIELDS
        if forbidden:
            raise ValueError(f"analysis {index} contains forbidden fact fields: {sorted(forbidden)}")
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"analysis {index} contains unknown fields: {sorted(unknown)}")
        paper_id = str(value.get("paper_id", ""))
        if paper_id not in valid_ids or paper_id in result:
            raise ValueError(f"analysis {index} has an unknown or duplicate paper_id")
        result[paper_id] = value
    return result
