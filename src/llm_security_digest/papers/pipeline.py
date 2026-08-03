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
from .sources import ArxivSource, CrossrefSource, GoogleScholarEnricher, OpenReviewSource


def default_client() -> HttpClient:
    email = os.getenv("LLMSD_CONTACT_EMAIL", "").strip()
    suffix = f" ({email})" if email else ""
    return HttpClient(user_agent=f"LLMSecurityDigest/2.0{suffix}")


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
        return not (plan.date_from or plan.date_to)
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


def collect(plan: SearchPlan, *, client: HttpClient | None = None) -> dict[str, Any]:
    client = client or default_client()
    adapters = {
        "arxiv": ArxivSource(client),
        "openreview": OpenReviewSource(client),
        "crossref": CrossrefSource(client, contact_email=os.getenv("LLMSD_CONTACT_EMAIL")),
    }
    candidates: dict[str, PaperFacts] = {}
    reports: list[dict[str, Any]] = []
    for source_name in plan.sources:
        try:
            discovered = adapters[source_name].discover(plan)
            accepted = 0
            for paper in discovered:
                if not _matches_keywords(paper, plan.filter_keywords) or not _matches_date_window(paper, plan):
                    continue
                existing = candidates.get(paper.paper_id)
                if existing and not _same_discovered_facts(existing, paper):
                    reports.append({"source": source_name, "status": "conflict", "paper_id": paper.paper_id})
                    continue
                candidates[paper.paper_id] = paper
                accepted += 1
            reports.append({"source": source_name, "status": "ok", "discovered": len(discovered), "accepted": accepted})
        except Exception as exc:
            reports.append({"source": source_name, "status": "error", "error_type": type(exc).__name__, "message": _error_message(exc, limit=300)})
    ordered = sorted(candidates.values(), key=lambda paper: (paper.published_at or "", paper.paper_id), reverse=True)
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "plan": asdict(plan),
        "source_reports": reports,
        "total": len(ordered),
        "candidates": [paper.to_dict() for paper in ordered],
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
        if bib_doi and bib_doi != normalize_doi(paper.doi):
            raise ValueError("BibTeX DOI does not match authoritative metadata")
    if paper.source == "arxiv":
        eprint = _extract_bibtex_field(bibtex, "eprint")
        url = _extract_bibtex_field(bibtex, "url")
        if not eprint and paper.source_id not in url:
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


def _authors_match(source_authors: list[str], bib_authors: list[str]) -> bool:
    if len(source_authors) != len(bib_authors):
        return False
    source_families = sorted(_author_family(author) for author in source_authors)
    bib_families = sorted(_author_family(author) for author in bib_authors)
    return bool(source_families) and source_families == bib_families and all(source_families)


def fetch_bibtex(paper: PaperFacts, *, client: HttpClient) -> tuple[str, str, dict[str, Any]]:
    if paper.source == "arxiv":
        url = f"https://arxiv.org/bibtex/{parse.quote(paper.source_id)}"
    elif paper.source == "openreview":
        url = f"https://openreview.net/bibtex?id={parse.quote(paper.source_id)}"
    elif paper.doi:
        url = f"https://api.crossref.org/works/{parse.quote(normalize_doi(paper.doi), safe='')}/transform/application/x-bibtex"
    else:
        raise ValueError("no authoritative BibTeX endpoint for paper")
    response = client.get(url, headers={"Accept": "application/x-bibtex"}, min_interval=0.25, max_bytes=2 * 1024 * 1024)
    bibtex = response.text().strip()
    validate_bibtex(paper, bibtex)
    provenance = {
        "source": "authoritative_bibtex_endpoint",
        "source_url": url,
        "fetched_at": utc_now(),
        "response_sha256": response.sha256,
        "extractor_version": "1",
    }
    return bibtex, url, provenance


def fetch_fulltext(paper: PaperFacts, *, client: HttpClient, data_dir: Path, max_bytes: int) -> dict[str, Any]:
    candidates: list[tuple[str, str]] = []
    if paper.source == "arxiv":
        candidates.append((f"https://arxiv.org/html/{parse.quote(paper.source_id)}", "html"))
    candidates.append((paper.pdf_url, "pdf"))
    failures: list[str] = []
    for url, kind in candidates:
        try:
            response = client.get(url, min_interval=0.25, max_bytes=max_bytes)
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


def refresh_authoritative(paper: PaperFacts, *, client: HttpClient) -> PaperFacts:
    """Re-fetch metadata for a selected ID so candidates cannot carry facts into output."""
    if paper.source == "arxiv":
        refreshed = ArxivSource(client).fetch_by_id(paper.source_id)
    elif paper.source == "openreview":
        refreshed = OpenReviewSource(client).fetch_by_id(paper.source_id)
    elif paper.source == "crossref" and paper.doi and paper.venue:
        refreshed = CrossrefSource(client).fetch_by_doi(paper.doi, expected_venue=paper.venue)
    else:
        raise ValueError(f"no authoritative identity refresh for source {paper.source!r}")
    if refreshed.paper_id != paper.paper_id:
        raise ValueError(
            f"authoritative identity mismatch: expected {paper.paper_id}, got {refreshed.paper_id}"
        )
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
    if not 1 <= int(target) <= 50:
        raise ValueError("target must be between 1 and 50")
    if int(scholar_limit) < 0 or int(scholar_limit) > 100:
        raise ValueError("scholar_limit must be between 0 and 100")
    client = client or default_client()
    raw_candidates = candidates_payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("candidates payload must contain a candidates array")
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
    for selection in selections:
        if len(verified) >= target:
            break
        paper = candidates.get(selection.paper_id)
        if paper is None:
            rejected.append({"paper_id": selection.paper_id, "reason": "unknown_paper_id"})
            continue
        try:
            paper.validate_discovered()
            paper = refresh_authoritative(paper, client=client)
            paper.validate_discovered()
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
