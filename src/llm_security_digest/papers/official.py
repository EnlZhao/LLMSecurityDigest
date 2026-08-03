"""Deterministic parsers for the registered official proceedings sources.

The adapters intentionally use only the Python standard library.  They parse
the stable metadata emitted by the official pages and keep partial records in
the incomplete queue instead of turning them into facts.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Iterable
from urllib.parse import quote_plus, urljoin, urlparse

from .http import HttpClient, HttpResponse
from .models import DiscoveryResult, PaperFacts, SearchPlan, VenueSpec, normalize_doi, normalize_title, utc_now


_OFFICIAL_PDF_HOSTS = {
    "acl": frozenset({"aclanthology.org"}),
    "emnlp": frozenset({"aclanthology.org"}),
    "icml": frozenset({"proceedings.mlr.press"}),
    "neurips": frozenset({"proceedings.neurips.cc"}),
    "aaai": frozenset({"ojs.aaai.org"}),
    "ijcai": frozenset({"www.ijcai.org"}),
    "usenix-security": frozenset({"www.usenix.org"}),
    "ndss": frozenset({"www.ndss-symposium.org"}),
}


class _Node:
    def __init__(self, tag: str = "root", attrs: dict[str, str] | None = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[_Node | str] = []

    def descendants(self) -> Iterable["_Node"]:
        for child in self.children:
            if isinstance(child, _Node):
                yield child
                yield from child.descendants()

    def text(self) -> str:
        values: list[str] = []
        for child in self.children:
            values.append(child if isinstance(child, str) else child.text())
        return " ".join("".join(values).split())


class _TreeParser(HTMLParser):
    _VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.casefold(), {str(key).casefold(): str(value or "") for key, value in attrs})
        self.stack[-1].children.append(node)
        if node.tag not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.casefold():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        wanted = tag.casefold()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == wanted:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


def _tree(value: str) -> _Node:
    parser = _TreeParser()
    parser.feed(value or "")
    parser.close()
    return parser.root


def _has_class(node: _Node, value: str) -> bool:
    return value.casefold() in {item.casefold() for item in node.attrs.get("class", "").split()}


def _nodes(root: _Node, *, tag: str | None = None, class_name: str | None = None, node_id: str | None = None) -> list[_Node]:
    result = []
    for node in root.descendants():
        if tag and node.tag != tag.casefold():
            continue
        if class_name and not _has_class(node, class_name):
            continue
        if node_id and node.attrs.get("id") != node_id:
            continue
        result.append(node)
    return result


def _first(root: _Node, *, tag: str | None = None, class_name: str | None = None, node_id: str | None = None) -> _Node | None:
    values = _nodes(root, tag=tag, class_name=class_name, node_id=node_id)
    return values[0] if values else None


def _clean(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).split())


def _meta(root: _Node, name: str) -> list[str]:
    wanted = name.casefold()
    return [
        _clean(node.attrs.get("content"))
        for node in _nodes(root, tag="meta")
        if node.attrs.get("name", "").casefold() == wanted and _clean(node.attrs.get("content"))
    ]


def _hrefs(root: _Node) -> list[tuple[str, str]]:
    return [(_clean(node.text()), _clean(node.attrs.get("href"))) for node in _nodes(root, tag="a") if node.attrs.get("href")]


def _absolute(value: str, base: str) -> str:
    return urljoin(base, value)


def _iso_date(value: str, fallback_year: int | None = None) -> str | None:
    text = _clean(value)
    formats = ("%Y-%m-%d", "%Y/%m/%d", "%Y/%m", "%B %d, %Y", "%b %d, %Y")
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return parsed.isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    match = re.search(r"\b((?:19|20)\d{2})\b", text)
    if match:
        return f"{match.group(1)}-01-01T00:00:00Z"
    if fallback_year:
        return f"{int(fallback_year):04d}-01-01T00:00:00Z"
    return None


def _authors(root: _Node, fallback: Iterable[str] = ()) -> list[str]:
    values = _meta(root, "citation_author")
    if values:
        return values
    for class_name in ("authors", "author", "paper-authors"):
        node = _first(root, class_name=class_name)
        if node:
            raw = re.sub(r"\s+and\s+", ",", node.text(), flags=re.IGNORECASE)
            values = [_clean(item) for item in re.split(r"[,;]", raw) if _clean(item)]
            if values:
                return values
    return [_clean(item) for item in fallback if _clean(item)]


def _abstract(root: _Node) -> str:
    # Only explicit abstract metadata/markup is authoritative.  Generic SEO
    # description tags frequently concatenate a title and author list and are
    # therefore intentionally not accepted as an abstract fallback.
    for name in ("citation_abstract", "DC.Description", "dc.Description"):
        values = _meta(root, name)
        if values:
            return _clean(values[0])
    for node_id in ("abstract", "Abstract"):
        node = _first(root, node_id=node_id)
        if node:
            return _strip_abstract_label(node.text())
    for node in _nodes(root):
        if any(_has_class(node, class_name) for class_name in ("abstract", "acl-abstract", "article-details-abstract", "abstract-content")):
            return _strip_abstract_label(node.text())
    return ""


def _strip_abstract_label(value: str) -> str:
    text = _clean(value)
    return re.sub(r"^abstract\s*:?[ \t]*", "", text, flags=re.IGNORECASE)


def _pdf_url(root: _Node, base: str) -> str:
    values = _meta(root, "citation_pdf_url")
    if values and values[0].startswith("https://"):
        return values[0]
    for label, href in _hrefs(root):
        absolute = _absolute(href, base)
        lowered = f"{label} {href}".casefold()
        if absolute.startswith("https://") and (absolute.casefold().endswith(".pdf") or "pdf" in lowered or label.casefold() in {"paper", "download"}):
            return absolute
    return ""


def _bibtex_url(root: _Node, base: str) -> str:
    """Find an explicit official BibTeX export link on a detail page."""

    for label, href in _hrefs(root):
        if "bibtex" in f"{label} {href}".casefold():
            absolute = _absolute(href, base)
            if absolute.startswith("https://"):
                return absolute
    return ""


def _bibtex_inline(root: _Node) -> str:
    # PMLR detail pages expose the official export in
    # ``<code class="citecode" id="bibtex">``.  Prefer the stable id before
    # broader class selectors so a page's citation UI cannot be mistaken for
    # the BibTeX payload.  Matching attributes case-insensitively keeps the
    # parser independent of HTML serializer casing.
    for node in root.descendants():
        node_id = node.attrs.get("id", "").casefold()
        classes = {item.casefold() for item in node.attrs.get("class", "").split()}
        if node_id != "bibtex" and not classes.intersection({"bibtex-text-entry", "bibtex", "citation-bibtex", "citecode"}):
            continue
        text = node.text().strip()
        if text.startswith("@"):
            return text
    return ""


def _doi(root: _Node) -> str | None:
    for value in _meta(root, "citation_doi") + _meta(root, "dc.identifier"):
        doi = normalize_doi(value.replace("doi:", "").strip())
        if doi.startswith("10."):
            return doi
    for _, href in _hrefs(root):
        if "doi.org/" in href.casefold():
            doi = normalize_doi(href.rsplit("/", 1)[-1])
            if doi.startswith("10."):
                return doi
    return None


def _source_provenance(response: HttpResponse | None, adapter: str) -> dict[str, Any]:
    return {
        "source": adapter,
        "source_url": response.url if response else None,
        "fetched_at": utc_now(),
        "response_sha256": response.sha256 if response else None,
        "extractor_version": "official-1",
    }


def _incomplete(
    spec: VenueSpec,
    *,
    adapter: str,
    source_id: str,
    landing_url: str,
    reason: str,
    missing: Iterable[str] = (),
    partial: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source": adapter,
        "adapter": adapter,
        "venue_group": spec.key,
        "source_id": source_id,
        "landing_url": landing_url,
        "reason": reason,
        "missing": sorted(set(missing)),
        "partial": partial or {},
    }


@dataclass
class ParsedRecord:
    paper: PaperFacts | None = None
    incomplete: dict[str, Any] | None = None


def _make_paper(
    *,
    spec: VenueSpec,
    adapter: str,
    source_id: str,
    title: str,
    authors: list[str],
    abstract: str,
    published_at: str | None,
    landing_url: str,
    pdf_url: str,
    doi: str | None,
    response: HttpResponse | None,
    source_metadata: dict[str, Any] | None = None,
) -> ParsedRecord:
    title = _clean(title)
    authors = [_clean(author) for author in authors if _clean(author)]
    abstract = _clean(abstract)
    missing = []
    if not title or not normalize_title(title):
        missing.append("title")
    if not authors:
        missing.append("authors")
    if not abstract:
        missing.append("abstract")
    if not landing_url.startswith("https://"):
        missing.append("landing_url")
    if not pdf_url.startswith("https://"):
        missing.append("pdf_url")
    pdf_host = urlparse(pdf_url).hostname if pdf_url else None
    allowed_pdf_hosts = _OFFICIAL_PDF_HOSTS.get(spec.key, frozenset())
    if not pdf_host or pdf_host.casefold().rstrip(".") not in allowed_pdf_hosts:
        if "pdf_url" not in missing:
            missing.append("pdf_url")
    if missing:
        return ParsedRecord(
            incomplete=_incomplete(
                spec,
                adapter=adapter,
                source_id=source_id,
                landing_url=landing_url,
                reason="required_official_field_missing",
                missing=missing,
                partial={"title": title, "authors": authors, "abstract": abstract, "doi": doi},
            )
        )
    provenance = _source_provenance(response, adapter)
    paper = PaperFacts(
        paper_id=f"{adapter}:{source_id}",
        source=adapter,
        source_id=source_id,
        title=title,
        authors=authors,
        abstract=abstract,
        publication_status="published",
        venue=spec.name,
        published_at=published_at,
        updated_at=None,
        doi=doi,
        landing_url=landing_url,
        pdf_url=pdf_url,
        platform_links={
            "primary": landing_url,
            "google_scholar": "https://scholar.google.com/scholar?q=" + quote_plus(f'"{title}"'),
            "semantic_scholar": "https://www.semanticscholar.org/search?q=" + quote_plus(f'"{title}"'),
            **({"doi": f"https://doi.org/{doi}"} if doi else {}),
        },
        identifiers={adapter: source_id, **({"doi": doi} if doi else {})},
        venue_evidence=[{
            "source": adapter,
            "venue_group": spec.key,
            "venue": spec.name,
            "verified": True,
        }],
        source_metadata={"adapter": adapter, "venue_group": spec.key, **(source_metadata or {})},
        collection_tier="formal",
        match_state="canonical",
        provenance={field: dict(provenance) for field in (
            "title", "authors", "abstract", "publication_status", "venue", "published_at", "doi", "landing_url", "pdf_url"
        )},
    )
    paper.validate_discovered()
    return ParsedRecord(paper=paper)


def _years_for_plan(plan: SearchPlan) -> list[int]:
    identifiers = plan.identifiers if isinstance(plan.identifiers, dict) else {}
    configured = identifiers.get("years")
    if isinstance(configured, dict):
        configured = configured.get("official") or configured.get("all")
    if isinstance(configured, (list, tuple)):
        years = sorted({int(item) for item in configured if int(item) > 0})
        if years:
            return years
    current_year = datetime.now(timezone.utc).year
    start = current_year - 2
    end = current_year
    if plan.date_from:
        start = int(plan.date_from[:4])
    if plan.date_to:
        end = int(plan.date_to[:4])
    return list(range(start, end + 1))


class OfficialAdapter:
    adapter = "official"

    def __init__(self, client: HttpClient):
        self.client = client

    def _get(self, url: str, *, max_bytes: int = 10 * 1024 * 1024) -> HttpResponse:
        hostname = urlparse(url).hostname
        allowed_hosts = {hostname} if hostname else None
        return self.client.get(url, min_interval=0.2, max_bytes=max_bytes, allowed_hosts=allowed_hosts)

    def _report(
        self,
        spec: VenueSpec,
        *,
        years: list[int],
        fetched: int,
        parsed: int,
        incomplete: list[dict[str, Any]],
        errors: list[dict[str, Any]],
        urls: list[str],
        truncated: bool = False,
        budget_exhausted: bool = False,
    ) -> dict[str, Any]:
        status = "error" if errors and not parsed else "partial" if errors or incomplete else "ok"
        request_urls = sorted(set(urls))
        records_incomplete = len(incomplete)
        records_scanned = parsed + records_incomplete
        requests_attempted = len(request_urls)
        requests_succeeded = min(max(int(fetched), 0), requests_attempted)
        return {
            "source": "official",
            "adapter": self.adapter,
            "venue_group": spec.key,
            "status": status,
            "years": years,
            # ``scanned``/``fetched`` remain compatibility fields.  They now
            # describe records and successful requests respectively; the
            # explicit request/record fields remove any ambiguity for
            # observability and replay.
            "scanned": records_scanned,
            "fetched": requests_succeeded,
            "parsed": parsed,
            "filtered": 0,
            "truncated": bool(truncated),
            "budget_exhausted": bool(budget_exhausted or truncated),
            "incomplete": records_incomplete,
            "records_scanned": records_scanned,
            "records_valid": parsed,
            "records_filtered": 0,
            "records_incomplete": records_incomplete,
            "requests_attempted": requests_attempted,
            "requests_succeeded": requests_succeeded,
            "requests_failed": max(requests_attempted - requests_succeeded, 0),
            "errors": errors,
            "urls": request_urls,
        }

    @staticmethod
    def _error(url: str, exc: Exception) -> dict[str, Any]:
        return {"url": url, "error_type": type(exc).__name__, "message": str(exc)[:300]}


def _last_path(value: str) -> str:
    path = urlparse(value).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or "unknown"


class ACLAnthologyAdapter(OfficialAdapter):
    adapter = "acl_anthology"
    VOLUMES = {
        "acl": (("acl-long", "long"), ("acl-short", "short"), ("findings-acl", "findings")),
        "emnlp": (("emnlp-main", "main"), ("findings-emnlp", "findings")),
    }

    @staticmethod
    def volume_paper_urls(html_text: str, *, year: int, volume_key: str) -> list[str]:
        root = _tree(html_text)
        # ACL volume pages include the proceedings volume itself as ``.0``;
        # only numbered paper records are authoritative paper candidates.
        pattern = re.compile(rf"/{int(year)}\.{re.escape(volume_key)}\.[1-9]\d*/?$")
        urls = {
            _absolute(href, "https://aclanthology.org")
            for _, href in _hrefs(root)
            if pattern.search(urlparse(_absolute(href, "https://aclanthology.org")).path)
        }
        return sorted(urls)

    @staticmethod
    def parse_paper(html_text: str, *, spec: VenueSpec, url: str, year: int, response: HttpResponse | None = None) -> ParsedRecord:
        root = _tree(html_text)
        title = (_meta(root, "citation_title") or [_clean((_first(root, tag="h1") or _Node()).text())])[0]
        authors = _authors(root)
        abstract = _abstract(root)
        pdf = _pdf_url(root, url) or f"{url.rstrip('/')}.pdf"
        published = next((_iso_date(value, year) for name in ("citation_publication_date", "citation_date", "DC.Date.issued") for value in _meta(root, name) if _iso_date(value, year)), _iso_date("", year))
        return _make_paper(
            spec=spec, adapter=spec.key, source_id=_last_path(url), title=title, authors=authors,
            abstract=abstract, published_at=published, landing_url=url, pdf_url=pdf,
            doi=_doi(root), response=response, source_metadata={
                "volume_year": year,
                "bibtex_url": f"{url.rstrip('/')}.bib",
            },
        )

    def discover(self, plan: SearchPlan, spec: VenueSpec) -> DiscoveryResult:
        years = _years_for_plan(plan)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        urls: list[str] = []
        fetched = 0
        limit = plan.max_results_per_venue
        for year in years:
            for volume_key, _label in self.VOLUMES.get(spec.key, ()):
                volume_url = f"https://aclanthology.org/volumes/{year}.{volume_key}/"
                urls.append(volume_url)
                try:
                    volume_response = self._get(volume_url)
                    fetched += 1
                    paper_urls = self.volume_paper_urls(volume_response.text(), year=year, volume_key=volume_key)
                except Exception as exc:
                    errors.append(self._error(volume_url, exc))
                    continue
                # Incomplete first entries (for example a withdrawn record)
                # must not prevent later valid papers in the same volume from
                # filling the bounded venue quota.
                for paper_url in paper_urls:
                    if len(papers) >= limit:
                        break
                    urls.append(paper_url)
                    try:
                        response = self._get(paper_url)
                        fetched += 1
                        parsed = self.parse_paper(response.text(), spec=spec, url=paper_url, year=year, response=response)
                        if parsed.paper:
                            papers.append(parsed.paper)
                        elif parsed.incomplete:
                            incomplete.append(parsed.incomplete)
                    except Exception as exc:
                        incomplete.append(_incomplete(spec, adapter=self.adapter, source_id=_last_path(paper_url), landing_url=paper_url, reason="official_detail_fetch_or_parse_failed", partial={"error_type": type(exc).__name__}))
                if len(papers) >= limit:
                    break
            if len(papers) >= limit:
                break
        return DiscoveryResult(papers, incomplete, [self._report(spec, years=years, fetched=fetched, parsed=len(papers), incomplete=incomplete, errors=errors, urls=urls, truncated=len(papers) >= plan.max_results_per_venue)])


class PMLRAdapter(OfficialAdapter):
    adapter = "pmlr"

    @staticmethod
    def volume_index(html_text: str) -> list[dict[str, Any]]:
        root = _tree(html_text)
        values = []
        # The live PMLR index puts the year in the list item which contains
        # the volume link, while older fixtures put it directly in the link.
        # Walk direct anchor children so the surrounding item text is kept
        # without relying on a particular class name.
        anchors: list[tuple[str, str, str]] = []
        for container in [root, *_nodes(root)]:
            for child in container.children:
                if not isinstance(child, _Node) or child.tag != "a":
                    continue
                href = _clean(child.attrs.get("href"))
                if href:
                    anchors.append((_clean(child.text()), href, _clean(container.text())))
        for label, href, parent_text in anchors:
            text = _clean(f"{label} {parent_text}")
            lowered = text.casefold()
            if "icml" not in lowered and "international conference on machine learning" not in lowered:
                continue
            match = re.search(r"\b((?:19|20)\d{2})\b", text)
            volume_match = re.search(r"(?:^|/)v(\d+)(?:/|$)", urlparse(href).path or href, flags=re.IGNORECASE)
            if not match or not volume_match:
                continue
            values.append({"year": int(match.group(1)), "volume": f"v{volume_match.group(1)}", "url": _absolute(href, "https://proceedings.mlr.press")})
        return sorted({(item["year"], item["volume"]): item for item in values}.values(), key=lambda item: (item["year"], item["volume"]))

    @staticmethod
    def paper_urls(html_text: str, *, volume: str) -> list[str]:
        root = _tree(html_text)
        pattern = re.compile(rf"/{re.escape(volume)}/[^/]+\.html(?:#.*)?$")
        return sorted({_absolute(href.split("#", 1)[0], "https://proceedings.mlr.press") for _, href in _hrefs(root) if pattern.search(href)})

    @staticmethod
    def parse_paper(html_text: str, *, spec: VenueSpec, url: str, year: int, response: HttpResponse | None = None) -> ParsedRecord:
        root = _tree(html_text)
        title = (_meta(root, "citation_title") or [_clean((_first(root, tag="h1") or _Node()).text())])[0]
        authors = _authors(root)
        abstract = _abstract(root)
        pdf = _pdf_url(root, url)
        if not pdf:
            pdf = _absolute(url.rsplit(".", 1)[0] + ".pdf", url)
        volume = urlparse(url).path.rstrip("/").split("/")[-2]
        source_metadata: dict[str, Any] = {
            "volume_year": year,
            "volume": volume,
            "bibtex_url": f"https://proceedings.mlr.press/{volume}/assets/bib/bibliography.bib",
        }
        inline_bibtex = _bibtex_inline(root)
        if inline_bibtex:
            source_metadata["bibtex_inline"] = inline_bibtex
        return _make_paper(
            spec=spec, adapter=self_adapter(spec, "pmlr"), source_id=f"{_last_path(url)}:{volume}",
            title=title, authors=authors, abstract=abstract, published_at=_iso_date("", year),
            landing_url=url, pdf_url=pdf, doi=_doi(root), response=response,
            source_metadata=source_metadata,
        )

    def discover(self, plan: SearchPlan, spec: VenueSpec) -> DiscoveryResult:
        years = _years_for_plan(plan)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        urls = ["https://proceedings.mlr.press/"]
        fetched = 0
        try:
            index_response = self._get(urls[0])
            fetched += 1
            volumes = [item for item in self.volume_index(index_response.text()) if item["year"] in years]
        except Exception as exc:
            errors.append(self._error(urls[0], exc))
            volumes = []
        for volume in volumes:
            if len(papers) >= plan.max_results_per_venue:
                break
            volume_url = f"https://proceedings.mlr.press/{volume['volume']}/"
            urls.append(volume_url)
            try:
                response = self._get(volume_url)
                fetched += 1
                paper_urls = self.paper_urls(response.text(), volume=volume["volume"])
            except Exception as exc:
                errors.append(self._error(volume_url, exc))
                continue
            for paper_url in paper_urls:
                if len(papers) >= plan.max_results_per_venue:
                    break
                urls.append(paper_url)
                try:
                    response = self._get(paper_url)
                    fetched += 1
                    parsed = self.parse_paper(response.text(), spec=spec, url=paper_url, year=volume["year"], response=response)
                    if parsed.paper:
                        papers.append(parsed.paper)
                    elif parsed.incomplete:
                        incomplete.append(parsed.incomplete)
                except Exception as exc:
                    incomplete.append(_incomplete(spec, adapter=self.adapter, source_id=_last_path(paper_url), landing_url=paper_url, reason="official_detail_fetch_or_parse_failed", partial={"error_type": type(exc).__name__}))
        return DiscoveryResult(papers, incomplete, [self._report(spec, years=years, fetched=fetched, parsed=len(papers), incomplete=incomplete, errors=errors, urls=urls, truncated=len(papers) >= plan.max_results_per_venue)])


def self_adapter(spec: VenueSpec, fallback: str) -> str:
    return spec.key if spec.key in {"acl", "emnlp"} else fallback


def parse_official_detail(
    html_text: str,
    *,
    spec: VenueSpec,
    url: str,
    source_id: str,
    fallback_year: int | None = None,
    response: HttpResponse | None = None,
) -> ParsedRecord:
    """Refresh one already-discovered official record from its detail page."""
    root = _tree(html_text)
    title = (_meta(root, "citation_title") or [_clean((_first(root, tag="h1") or _Node()).text())])[0]
    authors = _authors(root)
    abstract = _abstract(root)
    adapter_name = self_adapter(spec, spec.adapter or spec.key)
    pdf = _pdf_url(root, url)
    if not pdf:
        if spec.key in {"acl", "emnlp"}:
            pdf = f"{url.rstrip('/')}.pdf"
        elif spec.key == "icml" and url.casefold().endswith(".html"):
            pdf = f"{url.rsplit('.', 1)[0]}.pdf"
        elif spec.key == "neurips" and "-Abstract-Conference" in url:
            pdf = url.replace("-Abstract-Conference", "-Paper-Conference").replace(".html", ".pdf")
        elif spec.key == "ijcai":
            match = re.search(r"/proceedings/(?:19|20)\d{2}/(\d+)/?$", url)
            if match:
                year = re.search(r"/(?:19|20)\d{2}/", url)
                year_text = year.group(0).strip("/") if year else str(fallback_year or "")
                pdf = f"https://www.ijcai.org/proceedings/{year_text}/{int(match.group(1)):04d}.pdf"
    published = next(
        (_iso_date(value, fallback_year) for name in ("citation_publication_date", "citation_date", "DC.Date.issued") for value in _meta(root, name) if _iso_date(value, fallback_year)),
        _iso_date("", fallback_year),
    )
    source_metadata: dict[str, Any] = {"refreshed_from_detail": True}
    explicit_bibtex_url = _bibtex_url(root, url)
    if explicit_bibtex_url:
        source_metadata["bibtex_url"] = explicit_bibtex_url
    inline_bibtex = _bibtex_inline(root)
    if inline_bibtex:
        source_metadata["bibtex_inline"] = inline_bibtex
    elif spec.key == "acl" or spec.key == "emnlp":
        source_metadata["bibtex_url"] = f"{url.rstrip('/')}.bib"
    elif spec.adapter == "pmlr":
        path_parts = urlparse(url).path.rstrip("/").split("/")
        if len(path_parts) >= 2:
            source_metadata["bibtex_url"] = f"https://proceedings.mlr.press/{path_parts[-2]}/assets/bib/bibliography.bib"
    elif spec.key == "ijcai":
        match = re.search(r"/proceedings/(?:19|20)\d{2}/(\d+)/?$", url)
        if match:
            year_text = re.search(r"/((?:19|20)\d{2})/", url)
            source_metadata["bibtex_url"] = f"https://www.ijcai.org/proceedings/{year_text.group(1) if year_text else fallback_year}/bibtex/{int(match.group(1))}"
    return _make_paper(
        spec=spec,
        adapter=adapter_name,
        source_id=source_id,
        title=title,
        authors=authors,
        abstract=abstract,
        published_at=published,
        landing_url=url,
        pdf_url=pdf,
        doi=_doi(root),
        response=response,
        source_metadata=source_metadata,
    )


class NeurIPSAdapter(OfficialAdapter):
    adapter = "neurips"

    @staticmethod
    def index_url(year: int) -> str:
        return f"https://proceedings.neurips.cc/paper_files/paper/{int(year)}"

    @staticmethod
    def paper_urls(html_text: str, *, year: int) -> list[str]:
        root = _tree(html_text)
        prefix = f"/paper_files/paper/{int(year)}/"
        return sorted({_absolute(href.split("#", 1)[0], "https://proceedings.neurips.cc") for _, href in _hrefs(root) if prefix in urlparse(_absolute(href, "https://proceedings.neurips.cc")).path and "-Abstract-Conference" in href})

    @staticmethod
    def parse_paper(html_text: str, *, spec: VenueSpec, url: str, year: int, response: HttpResponse | None = None) -> ParsedRecord:
        root = _tree(html_text)
        title = (_meta(root, "citation_title") or [_clean((_first(root, tag="h1") or _Node()).text())])[0]
        authors = _authors(root)
        abstract = _abstract(root)
        pdf = _pdf_url(root, url)
        if not pdf and "-Abstract-Conference" in url:
            pdf = url.replace("-Abstract-Conference", "-Paper-Conference").replace(".html", ".pdf")
        return _make_paper(
            spec=spec, adapter=self_adapter(spec, "neurips"), source_id=_last_path(url).replace("-Abstract-Conference.html", ""),
            title=title, authors=authors, abstract=abstract, published_at=_iso_date("", year), landing_url=url,
            pdf_url=pdf, doi=_doi(root), response=response, source_metadata={
                "proceedings_year": year,
                **({"bibtex_url": _bibtex_url(root, url)} if _bibtex_url(root, url) else {}),
            },
        )

    def discover(self, plan: SearchPlan, spec: VenueSpec) -> DiscoveryResult:
        years = _years_for_plan(plan)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        urls: list[str] = []
        fetched = 0
        for year in years:
            index_url = self.index_url(year)
            urls.append(index_url)
            try:
                response = self._get(index_url)
                fetched += 1
                paper_urls = self.paper_urls(response.text(), year=year)
            except Exception as exc:
                errors.append(self._error(index_url, exc))
                continue
            for paper_url in paper_urls:
                if len(papers) >= plan.max_results_per_venue:
                    break
                urls.append(paper_url)
                try:
                    response = self._get(paper_url)
                    fetched += 1
                    parsed = self.parse_paper(response.text(), spec=spec, url=paper_url, year=year, response=response)
                    if parsed.paper:
                        papers.append(parsed.paper)
                    elif parsed.incomplete:
                        incomplete.append(parsed.incomplete)
                except Exception as exc:
                    incomplete.append(_incomplete(spec, adapter=self.adapter, source_id=_last_path(paper_url), landing_url=paper_url, reason="official_detail_fetch_or_parse_failed", partial={"error_type": type(exc).__name__}))
        return DiscoveryResult(papers, incomplete, [self._report(spec, years=years, fetched=fetched, parsed=len(papers), incomplete=incomplete, errors=errors, urls=urls, truncated=len(papers) >= plan.max_results_per_venue)])


class AAAIOJSAdapter(OfficialAdapter):
    adapter = "aaai_ojs"
    ARCHIVE_URL = "https://ojs.aaai.org/index.php/AAAI/issue/archive"
    MAX_ARCHIVE_PAGES = 12

    @staticmethod
    def issue_year(title: str) -> int | None:
        match = re.search(r"\bAAAI[- ](\d{2})\b", title, flags=re.IGNORECASE)
        return 2000 + int(match.group(1)) if match else None

    @classmethod
    def issue_page_entries(cls, html_text: str, *, base_url: str | None = None) -> list[dict[str, Any]]:
        root = _tree(html_text)
        values: list[dict[str, Any]] = []
        for node in _nodes(root, class_name="obj_issue_summary"):
            title_node = next((child for child in node.descendants() if child.tag == "a" and "title" in child.attrs.get("class", "").split()), None)
            title = _clean(title_node.text() if title_node else "")
            href = _clean(title_node.attrs.get("href") if title_node else "")
            year = cls.issue_year(title)
            if year is not None and href:
                values.append({"title": title, "year": year, "url": _absolute(href, base_url or cls.ARCHIVE_URL)})
        return list({item["url"]: item for item in values}.values())

    @classmethod
    def issue_urls(cls, html_text: str, *, years: Iterable[int], base_url: str | None = None) -> list[dict[str, Any]]:
        wanted = {int(year) for year in years}
        values = [
            item for item in cls.issue_page_entries(html_text, base_url=base_url)
            if item["year"] in wanted and "technical tracks" in item["title"].casefold()
        ]
        return sorted({item["url"]: item for item in values}.values(), key=lambda item: (item["year"], item["url"]))

    @classmethod
    def next_archive_url(cls, html_text: str, *, base_url: str) -> str | None:
        root = _tree(html_text)
        for node in _nodes(root, tag="a"):
            label = _clean(node.text()).casefold()
            classes = {item.casefold() for item in node.attrs.get("class", "").split()}
            if label == "next" or "next" in classes:
                href = _clean(node.attrs.get("href"))
                if href:
                    return _absolute(href, base_url)
        return None

    @staticmethod
    def article_summaries(html_text: str, *, issue: dict[str, Any]) -> list[dict[str, Any]]:
        root = _tree(html_text)
        values = []
        for node in _nodes(root, class_name="obj_article_summary"):
            # OJS versions differ on where the ``title`` class lives: older
            # pages put it on the anchor, while current AAAI pages put it on
            # an ``h3`` that contains the anchor.  In both cases the anchor
            # owns the stable article URL; the heading supplies a fallback
            # title when the anchor itself has no text.
            title_node = next(
                (
                    child
                    for child in node.descendants()
                    if child.tag == "a" and _has_class(child, "title")
                ),
                None,
            )
            title_heading = next(
                (
                    child
                    for child in node.descendants()
                    if _has_class(child, "title")
                ),
                None,
            )
            if title_node is None and title_heading is not None:
                title_node = next(
                    (child for child in title_heading.descendants() if child.tag == "a"),
                    title_heading,
                )
            href = _clean(title_node.attrs.get("href") if title_node else "")
            title = _clean(title_node.text() if title_node else "")
            if not title and title_heading is not None:
                title = _clean(title_heading.text())
            article_match = re.search(r"/article/view/(\d+)", href)
            if not article_match or not title:
                continue
            article_url = _absolute(href, AAAIOJSAdapter.ARCHIVE_URL)
            pdf = ""
            for label, link in _hrefs(node):
                if "pdf" in label.casefold() or "pdf" in link.casefold():
                    pdf = _absolute(link, article_url)
                    break
            values.append({"article_id": article_match.group(1), "url": article_url, "title": title, "pdf_url": pdf, "authors": _authors(node), "year": issue["year"]})
        return values

    @staticmethod
    def parse_article(html_text: str, *, spec: VenueSpec, summary: dict[str, Any], response: HttpResponse | None = None) -> ParsedRecord:
        root = _tree(html_text)
        title = (_meta(root, "citation_title") or [summary.get("title", "")])[0]
        authors = _authors(root, summary.get("authors") or [])
        abstract = _abstract(root)
        pdf = _pdf_url(root, summary.get("url", "")) or summary.get("pdf_url", "")
        return _make_paper(
            spec=spec, adapter=self_adapter(spec, "aaai_ojs"), source_id=str(summary["article_id"]), title=title,
            authors=authors, abstract=abstract, published_at=_iso_date("", int(summary["year"])),
            landing_url=summary["url"], pdf_url=pdf, doi=_doi(root), response=response,
            source_metadata={"issue_year": summary["year"], "article_id": summary["article_id"]},
        )

    def discover(self, plan: SearchPlan, spec: VenueSpec) -> DiscoveryResult:
        years = _years_for_plan(plan)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        urls: list[str] = []
        fetched = 0
        issues_by_url: dict[str, dict[str, Any]] = {}
        archive_url = self.ARCHIVE_URL
        seen_archive_urls: set[str] = set()
        minimum_year = min(years) if years else 0
        for _page in range(self.MAX_ARCHIVE_PAGES):
            if not archive_url or archive_url in seen_archive_urls:
                break
            seen_archive_urls.add(archive_url)
            urls.append(archive_url)
            try:
                response = self._get(archive_url)
                fetched += 1
                page_entries = self.issue_page_entries(response.text(), base_url=archive_url)
            except Exception as exc:
                errors.append(self._error(archive_url, exc))
                break
            for issue in page_entries:
                if issue["year"] in years and "technical tracks" in issue["title"].casefold():
                    issues_by_url.setdefault(issue["url"], issue)
            page_years = [int(issue["year"]) for issue in page_entries]
            if page_years and max(page_years) < minimum_year:
                break
            archive_url = self.next_archive_url(response.text(), base_url=archive_url)
        issues = sorted(issues_by_url.values(), key=lambda item: (item["year"], item["url"]))
        for issue in issues:
            if len(papers) >= plan.max_results_per_venue:
                break
            issue_url = issue["url"]
            urls.append(issue_url)
            try:
                response = self._get(issue_url)
                fetched += 1
                summaries = self.article_summaries(response.text(), issue=issue)
            except Exception as exc:
                errors.append(self._error(issue_url, exc))
                continue
            for summary in summaries:
                if len(papers) >= plan.max_results_per_venue:
                    break
                urls.append(summary["url"])
                try:
                    response = self._get(summary["url"])
                    fetched += 1
                    parsed = self.parse_article(response.text(), spec=spec, summary=summary, response=response)
                    if parsed.paper:
                        papers.append(parsed.paper)
                    elif parsed.incomplete:
                        incomplete.append(parsed.incomplete)
                except Exception as exc:
                    incomplete.append(_incomplete(spec, adapter=self.adapter, source_id=summary["article_id"], landing_url=summary["url"], reason="official_detail_fetch_or_parse_failed", partial={"error_type": type(exc).__name__}))
        return DiscoveryResult(papers, incomplete, [self._report(spec, years=years, fetched=fetched, parsed=len(papers), incomplete=incomplete, errors=errors, urls=urls, truncated=len(papers) >= plan.max_results_per_venue)])


class IJCAIAdapter(OfficialAdapter):
    adapter = "ijcai"

    @staticmethod
    def parse_papers(html_text: str, *, spec: VenueSpec, year: int, base_url: str, detail_loader: Callable[[str], tuple[str, HttpResponse | None]] | None = None) -> list[ParsedRecord]:
        root = _tree(html_text)
        records: list[ParsedRecord] = []
        for node in _nodes(root, class_name="paper_wrapper"):
            title_node = _first(node, class_name="title")
            details = _first(node, class_name="details")
            title = title_node.text() if title_node else ""
            raw_authors = re.sub(r"\s+and\s+", ",", details.text() if details else "", flags=re.IGNORECASE)
            authors = [_clean(item) for item in raw_authors.split(",") if _clean(item)]
            pdf = next((link for label, link in _hrefs(node) if link.casefold().endswith(".pdf")), "")
            landing = next((link for _label, link in _hrefs(node) if re.search(r"/proceedings/\d{4}/\d+/?$", link)), "")
            pdf_url = _absolute(pdf, base_url) if pdf else ""
            landing_url = _absolute(landing, base_url) if landing else ""
            match = re.search(r"/(\d+)\.pdf$", pdf_url) or re.search(r"/proceedings/\d{4}/(\d+)/?$", landing_url)
            paper_num = match.group(1) if match else ""
            if paper_num and not landing_url:
                landing_url = f"https://www.ijcai.org/proceedings/{year}/{paper_num}"
            abstract_node = _first(node, class_name="abstract")
            abstract = abstract_node.text() if abstract_node else ""
            response = None
            if not abstract and detail_loader and landing_url:
                try:
                    detail_html, response = detail_loader(landing_url)
                    detail_root = _tree(detail_html)
                    abstract = _abstract(detail_root)
                except Exception as exc:
                    records.append(ParsedRecord(incomplete=_incomplete(
                        spec,
                        adapter=self_adapter(spec, "ijcai"),
                        source_id=f"{year}-{paper_num or 'unknown'}",
                        landing_url=landing_url,
                        reason="official_detail_fetch_failed",
                        missing=("abstract",),
                        partial={"error_type": type(exc).__name__, "message": str(exc)[:300]},
                    )))
                    continue
            if paper_num and not pdf_url:
                pdf_url = f"https://www.ijcai.org/proceedings/{year}/{int(paper_num):04d}.pdf"
            records.append(_make_paper(
                spec=spec,
                adapter=self_adapter(spec, "ijcai"),
                source_id=f"{year}-{paper_num or 'unknown'}",
                title=title,
                authors=authors,
                abstract=abstract,
                published_at=_iso_date("", year),
                landing_url=landing_url,
                pdf_url=pdf_url,
                doi=None,
                response=response,
                source_metadata={
                    "proceedings_year": year,
                    "paper_number": paper_num,
                    **({"bibtex_url": f"https://www.ijcai.org/proceedings/{year}/bibtex/{int(paper_num)}"} if paper_num else {}),
                },
            ))
        return records

    def discover(self, plan: SearchPlan, spec: VenueSpec) -> DiscoveryResult:
        years = _years_for_plan(plan)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        urls: list[str] = []
        fetched = 0
        for year in years:
            list_url = f"https://www.ijcai.org/proceedings/{year}/"
            urls.append(list_url)
            try:
                response = self._get(list_url)
                fetched += 1
                def load_detail(url: str) -> tuple[str, HttpResponse | None]:
                    nonlocal fetched
                    detail_response = self._get(url)
                    fetched += 1
                    return detail_response.text(), detail_response
                records = self.parse_papers(response.text(), spec=spec, year=year, base_url=list_url, detail_loader=load_detail)
                for record in records:
                    if len(papers) >= plan.max_results_per_venue:
                        break
                    if record.paper:
                        papers.append(record.paper)
                    elif record.incomplete:
                        incomplete.append(record.incomplete)
            except Exception as exc:
                errors.append(self._error(list_url, exc))
        return DiscoveryResult(papers, incomplete, [self._report(spec, years=years, fetched=fetched, parsed=len(papers), incomplete=incomplete, errors=errors, urls=urls, truncated=len(papers) >= plan.max_results_per_venue)])


class USENIXAdapter(OfficialAdapter):
    adapter = "usenix"

    @staticmethod
    def presentation_urls(html_text: str, *, year: int, base_url: str) -> list[str]:
        prefix = f"/conference/usenixsecurity{int(year) % 100:02d}/presentation/"
        return sorted({_absolute(href.split("#", 1)[0], base_url) for _, href in _hrefs(_tree(html_text)) if prefix in urlparse(_absolute(href, base_url)).path})

    @staticmethod
    def parse_paper(html_text: str, *, spec: VenueSpec, url: str, year: int, response: HttpResponse | None = None) -> ParsedRecord:
        root = _tree(html_text)
        title = (_meta(root, "citation_title") or [_clean((_first(root, tag="h1") or _Node()).text())])[0]
        authors = _authors(root)
        abstract = _abstract(root)
        pdf = _pdf_url(root, url)
        source_metadata = {"conference_year": year}
        bibtex_url = _bibtex_url(root, url)
        bibtex_inline = _bibtex_inline(root)
        if bibtex_url:
            source_metadata["bibtex_url"] = bibtex_url
        if bibtex_inline:
            source_metadata["bibtex_inline"] = bibtex_inline
        return _make_paper(
            spec=spec,
            adapter=self_adapter(spec, "usenix"),
            source_id=_last_path(url),
            title=title,
            authors=authors,
            abstract=abstract,
            published_at=_iso_date(next(iter(_meta(root, "citation_publication_date")), ""), year),
            landing_url=url,
            pdf_url=pdf,
            doi=_doi(root),
            response=response,
            source_metadata=source_metadata,
        )

    def discover(self, plan: SearchPlan, spec: VenueSpec) -> DiscoveryResult:
        years = _years_for_plan(plan)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        urls: list[str] = []
        fetched = 0
        for year in years:
            list_url = f"https://www.usenix.org/conference/usenixsecurity{int(year) % 100:02d}/technical-sessions"
            urls.append(list_url)
            try:
                response = self._get(list_url)
                fetched += 1
                paper_urls = self.presentation_urls(response.text(), year=year, base_url=list_url)
            except Exception as exc:
                errors.append(self._error(list_url, exc))
                continue
            for paper_url in paper_urls:
                if len(papers) >= plan.max_results_per_venue:
                    break
                urls.append(paper_url)
                try:
                    response = self._get(paper_url)
                    fetched += 1
                    parsed = self.parse_paper(response.text(), spec=spec, url=paper_url, year=year, response=response)
                    if parsed.paper:
                        papers.append(parsed.paper)
                    elif parsed.incomplete:
                        incomplete.append(parsed.incomplete)
                except Exception as exc:
                    incomplete.append(_incomplete(spec, adapter=self.adapter, source_id=_last_path(paper_url), landing_url=paper_url, reason="official_detail_fetch_or_parse_failed", partial={"error_type": type(exc).__name__}))
        return DiscoveryResult(papers, incomplete, [self._report(spec, years=years, fetched=fetched, parsed=len(papers), incomplete=incomplete, errors=errors, urls=urls, truncated=len(papers) >= plan.max_results_per_venue)])


class NDSSAdapter(OfficialAdapter):
    adapter = "ndss"

    @staticmethod
    def paper_urls(html_text: str, *, base_url: str) -> list[str]:
        return sorted({_absolute(href.split("#", 1)[0], base_url) for _, href in _hrefs(_tree(html_text)) if "/ndss-paper/" in href})

    @staticmethod
    def parse_paper(html_text: str, *, spec: VenueSpec, url: str, year: int, response: HttpResponse | None = None) -> ParsedRecord:
        root = _tree(html_text)
        title = (_meta(root, "citation_title") or [_clean((_first(root, tag="h1") or _Node()).text())])[0]
        authors = _authors(root)
        if not authors:
            node = _first(root, class_name="paper-data") or _first(root, class_name="entry-content")
            paragraphs = [item.text() for item in _nodes(node) if item.tag == "p"] if node else []
            if paragraphs:
                authors = [_clean(item) for item in re.split(r"[,;]", paragraphs[0]) if _clean(item)]
        abstract = _abstract(root)
        pdf = _pdf_url(root, url)
        return _make_paper(spec=spec, adapter=self_adapter(spec, "ndss"), source_id=_last_path(url), title=title, authors=authors, abstract=abstract, published_at=_iso_date("", year), landing_url=url, pdf_url=pdf, doi=_doi(root), response=response, source_metadata={"symposium_year": year})

    def discover(self, plan: SearchPlan, spec: VenueSpec) -> DiscoveryResult:
        years = _years_for_plan(plan)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        urls: list[str] = []
        fetched = 0
        for year in years:
            list_url = f"https://www.ndss-symposium.org/ndss{year}/accepted-papers/"
            urls.append(list_url)
            try:
                response = self._get(list_url)
                fetched += 1
                paper_urls = self.paper_urls(response.text(), base_url=list_url)
            except Exception as exc:
                errors.append(self._error(list_url, exc))
                continue
            for paper_url in paper_urls:
                if len(papers) >= plan.max_results_per_venue:
                    break
                urls.append(paper_url)
                try:
                    response = self._get(paper_url)
                    fetched += 1
                    parsed = self.parse_paper(response.text(), spec=spec, url=paper_url, year=year, response=response)
                    if parsed.paper:
                        papers.append(parsed.paper)
                    elif parsed.incomplete:
                        incomplete.append(parsed.incomplete)
                except Exception as exc:
                    incomplete.append(_incomplete(spec, adapter=self.adapter, source_id=_last_path(paper_url), landing_url=paper_url, reason="official_detail_fetch_or_parse_failed", partial={"error_type": type(exc).__name__}))
        return DiscoveryResult(papers, incomplete, [self._report(spec, years=years, fetched=fetched, parsed=len(papers), incomplete=incomplete, errors=errors, urls=urls, truncated=len(papers) >= plan.max_results_per_venue)])


ADAPTERS: dict[str, type[OfficialAdapter]] = {
    "acl_anthology": ACLAnthologyAdapter,
    "pmlr": PMLRAdapter,
    "neurips": NeurIPSAdapter,
    "aaai_ojs": AAAIOJSAdapter,
    "ijcai": IJCAIAdapter,
    "usenix": USENIXAdapter,
    "ndss": NDSSAdapter,
}
