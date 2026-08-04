"""Deterministic parsers for the registered official proceedings sources.

The adapters intentionally use only the Python standard library.  They parse
the stable metadata emitted by the official pages and keep partial records in
the incomplete queue instead of turning them into facts.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlparse

from .http import HttpClient, HttpResponse, _secret_query_key
from .models import DiscoveryResult, PaperFacts, SearchPlan, VenueSpec, get_venue_spec, normalize_doi, normalize_title, utc_now


_OFFICIAL_PDF_HOSTS = {
    "acl": frozenset({"aclanthology.org"}),
    "emnlp": frozenset({"aclanthology.org"}),
    "icml": frozenset({"proceedings.mlr.press", "raw.githubusercontent.com"}),
    "neurips": frozenset({"proceedings.neurips.cc"}),
    "aaai": frozenset({"ojs.aaai.org"}),
    "ijcai": frozenset({"www.ijcai.org"}),
    "usenix-security": frozenset({"www.usenix.org"}),
    "ndss": frozenset({"www.ndss-symposium.org"}),
    "cvpr": frozenset({"openaccess.thecvf.com"}),
    "eccv": frozenset({"www.ecva.net"}),
    # CSDL's stable download endpoint lives on ``www.computer.org``.  The
    # endpoint may redirect to a short-lived signed object on the explicit
    # IEEE Computer Society download host; that host is accepted only while
    # verifying the response, never emitted as the candidate PDF URL.
    "ieee-sp": frozenset({"www.computer.org"}),
}

_OFFICIAL_INDEX_HOSTS = {
    "acl_anthology": frozenset({"aclanthology.org"}),
    "pmlr": frozenset({"proceedings.mlr.press"}),
    "neurips": frozenset({"proceedings.neurips.cc"}),
    "ecva": frozenset({"www.ecva.net"}),
    "cvf": frozenset({"openaccess.thecvf.com"}),
    "aaai_ojs": frozenset({"ojs.aaai.org"}),
    "ijcai": frozenset({"www.ijcai.org"}),
    "usenix": frozenset({"www.usenix.org"}),
    "ndss": frozenset({"www.ndss-symposium.org"}),
    "ieee_csdl": frozenset({"www.computer.org"}),
}

_DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)


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


def _official_link(value: str, base: str, allowed_hosts: Iterable[str]) -> str:
    """Resolve an official-page link only when it stays on a registered host."""
    absolute = _absolute(value.split("#", 1)[0], base)
    parsed = urlparse(absolute)
    hostname = parsed.hostname.casefold().rstrip(".") if parsed.hostname else ""
    hosts = {str(item).casefold().rstrip(".") for item in allowed_hosts}
    if parsed.scheme.casefold() != "https" or hostname not in hosts:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    return absolute


def _iso_date(value: str) -> str | None:
    text = _clean(value)
    formats = ("%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y")
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return parsed.isoformat().replace("+00:00", "Z")
        except ValueError:
            pass
    # A proceedings year is a routing hint, not an exact publication date.
    # Facts must not turn it into a fabricated January 1 timestamp.
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
        if any(_has_class(node, class_name) for class_name in ("abstract", "paper-abstract", "acl-abstract", "article-details-abstract", "abstract-content")):
            return _strip_abstract_label(node.text())
    return ""


def _strip_abstract_label(value: str) -> str:
    text = _clean(value)
    return re.sub(r"^abstract\s*:?[ \t]*", "", text, flags=re.IGNORECASE)


def _split_top_level(value: str, delimiter: str = ",") -> list[str]:
    """Split a delimited field without breaking parenthesized affiliations."""
    values: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(depth - 1, 0)
        elif depth == 0 and value.startswith(delimiter, index):
            item = _clean(value[start:index])
            if item:
                values.append(item)
            start = index + len(delimiter)
    item = _clean(value[start:])
    if item:
        values.append(item)
    return values


def _author_list(value: str) -> list[str]:
    """Normalize comma/``and`` separated author labels from proceedings pages."""
    value = re.sub(r"\s+and\s+", ",", value, flags=re.IGNORECASE)
    return [_clean(item) for item in _split_top_level(value) if _clean(item)]


def _source_year(source_id: str, fallback_year: int | None = None) -> int | None:
    match = re.match(r"((?:19|20)\d{2}):", str(source_id or ""))
    if match:
        return int(match.group(1))
    return int(fallback_year) if fallback_year is not None else None


def _pdf_url(root: _Node, base: str) -> str:
    values = _meta(root, "citation_pdf_url")
    if values and values[0].startswith("https://"):
        return values[0]
    for label, href in _hrefs(root):
        absolute = _absolute(href, base)
        parsed = urlparse(absolute)
        # A generic "Download" link can be a citation export or supplementary
        # archive. Only accept a URL that identifies itself as a PDF.
        if absolute.startswith("https://") and (
            parsed.path.casefold().endswith(".pdf")
            or ".pdf/" in parsed.path.casefold()
        ):
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
        if node_id != "bibtex" and not classes.intersection({"bibtex-text-entry", "bibtex", "citation-bibtex", "citecode", "bibref"}):
            continue
        text = node.text().strip()
        if text.startswith("@"):
            return text
    return ""


def _doi(root: _Node) -> str | None:
    for value in _meta(root, "citation_doi") + _meta(root, "dc.identifier"):
        doi = normalize_doi(value.replace("doi:", "").strip())
        if _DOI_RE.fullmatch(doi):
            return doi
    for _, href in _hrefs(root):
        if "doi.org/" in href.casefold():
            doi = normalize_doi(href.rsplit("/", 1)[-1])
            if _DOI_RE.fullmatch(doi):
                return doi
    return None


def _source_provenance(response: HttpResponse | None, adapter: str) -> dict[str, Any]:
    return {
        "source": adapter,
        "source_url": response.url if response else None,
        "final_url": response.final_url if response else None,
        "transport": response.transport if response else None,
        "redirect_chain": list(response.redirect_chain) if response else [],
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
    elif spec.key == "icml" and pdf_host.casefold().rstrip(".") == "raw.githubusercontent.com":
        parsed_pdf = urlparse(pdf_url)
        path = parsed_pdf.path
        paper_key, separator, volume = source_id.rpartition(":")
        if (
            not separator
            or parsed_pdf.query
            or parsed_pdf.username is not None
            or parsed_pdf.password is not None
            or not re.fullmatch(
                rf"/mlresearch/{re.escape(volume)}/main/assets/{re.escape(paper_key)}/{re.escape(paper_key)}\.pdf",
                path,
            )
        ):
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

    def __init__(self, client: HttpClient, *, route_catalog: Any | None = None):
        self.client = client
        # The catalog is an optional read-only index.  Adapters still fetch
        # the selected URL through the normal bounded client and parse the
        # response using their baseline rules.
        self.route_catalog = route_catalog

    def _route_hint(
        self,
        spec: VenueSpec | None,
        *,
        route_kind: str | None,
        source: str = "official",
        fallback_url: str | None = None,
    ) -> str | None:
        if self.route_catalog is None or spec is None or not route_kind:
            return None
        try:
            routes = self.route_catalog.verified_routes(venue=spec)
        except Exception:
            return None
        expected_adapter = self.adapter.casefold()
        expected_kind = route_kind.casefold()
        expected_source = source.casefold()
        for route in routes:
            # ``verified_routes`` is the baseline catalog contract, but keep
            # the adapter boundary defensive when a custom catalog/view is
            # injected.  A failed row or a row returned for another venue is
            # never a reusable hint.
            verification_state = getattr(route, "verification_state", None)
            if (
                verification_state is not None
                and str(verification_state).casefold() != "verified"
            ):
                continue
            verified = getattr(route, "verified", None)
            if verified is not None and not bool(verified):
                continue
            route_venue = getattr(route, "venue_key", None)
            if route_venue is not None:
                route_spec = get_venue_spec(route_venue)
                if route_spec is None or route_spec.key.casefold() != spec.key.casefold():
                    continue
            if (
                str(getattr(route, "source", "")).casefold() == expected_source
                and str(getattr(route, "adapter", "")).casefold() == expected_adapter
                and str(getattr(route, "route_kind", "")).casefold() == expected_kind
                and isinstance(getattr(route, "url", None), str)
                and self._hint_url_allowed(route.url)
                and self._hint_path_allowed(route.url, spec=spec, route_kind=route_kind)
                and self._hint_year_allowed(route.url, fallback_url=fallback_url)
                and self._hint_scope_allowed(
                    route.url,
                    fallback_url=fallback_url,
                    allow_query_mismatch=self.adapter.casefold() == "ieee_csdl",
                )
            ):
                return route.url
        return None

    def _hint_path_allowed(
        self,
        url: str,
        *,
        spec: VenueSpec,
        route_kind: str,
    ) -> bool:
        """Apply the adapter's baseline path grammar to an index hint."""
        if route_kind.casefold() != "index":
            return True
        parsed = urlparse(url)
        path = parsed.path or "/"
        adapter = self.adapter.casefold()
        if adapter == "acl_anthology":
            return bool(re.fullmatch(r"/volumes/(?:19|20)\d{2}\.[A-Za-z0-9][A-Za-z0-9_-]*/?", path))
        if adapter == "pmlr":
            return path == "/" and not parsed.query
        if adapter == "neurips":
            return bool(re.fullmatch(r"/paper_files/paper/(?:19|20)\d{2}/?", path)) and not parsed.query
        if adapter == "ecva":
            return path == "/papers.php" and not parsed.query
        if adapter == "cvf":
            code = re.escape(spec.key.upper())
            return bool(re.fullmatch(rf"/{code}(?:19|20)\d{{2}}", path)) and parsed.query.casefold() == "day=all"
        if adapter == "aaai_ojs":
            return path.rstrip("/") == "/index.php/AAAI/issue/archive"
        if adapter == "ieee_csdl":
            return path == "/csdl/api/v1/graphql"
        if adapter == "ijcai":
            return bool(re.fullmatch(r"/proceedings/(?:19|20)\d{2}/?", path)) and not parsed.query
        if adapter == "usenix":
            return bool(re.fullmatch(r"/conference/usenixsecurity\d{2}/technical-sessions/?", path)) and not parsed.query
        if adapter == "ndss":
            return bool(re.fullmatch(r"/ndss(?:19|20)\d{2}/accepted-papers/?", path)) and not parsed.query
        return True

    def _hint_url_allowed(self, url: str) -> bool:
        """Re-check a runtime hint before allowing it to reach the client."""
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        if (
            parsed.scheme.casefold() != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.fragment
            or hostname not in _OFFICIAL_INDEX_HOSTS.get(self.adapter.casefold(), frozenset())
        ):
            return False
        try:
            return not any(_secret_query_key(key) for key, _value in parse_qsl(parsed.query, keep_blank_values=True))
        except ValueError:
            return False

    @staticmethod
    def _hint_year_allowed(url: str, *, fallback_url: str | None) -> bool:
        """Keep a verified year-specific hint scoped to the requested year."""
        if not fallback_url:
            return True

        def years(value: str) -> set[str]:
            parsed = urlparse(value)
            found = set(re.findall(r"(?:19|20)\d{2}", parsed.path))
            short = re.findall(r"usenixsecurity(\d{2})", parsed.path, flags=re.IGNORECASE)
            found.update(f"20{item}" for item in short)
            return found

        expected = years(fallback_url)
        candidate = years(url)
        return not expected or not candidate or bool(expected & candidate)

    @staticmethod
    def _hint_scope_allowed(
        url: str,
        *,
        fallback_url: str | None,
        allow_query_mismatch: bool = False,
    ) -> bool:
        """Keep volume/pagination-specific index hints on their own scope."""
        if not fallback_url:
            return True
        fallback = urlparse(fallback_url)
        candidate = urlparse(url)
        # A query-bearing hint can represent a pagination page (for example
        # the AAAI archive). Never substitute it for a queryless baseline,
        # and keep query-bearing baselines on their exact scope.
        if not allow_query_mismatch and fallback.query != candidate.query:
            return False
        fallback_volume = re.search(r"/volumes/([^/]+)/?", fallback.path)
        candidate_volume = re.search(r"/volumes/([^/]+)/?", candidate.path)
        if fallback_volume and candidate_volume:
            return fallback_volume.group(1).casefold() == candidate_volume.group(1).casefold()
        return True

    def _get(
        self,
        url: str,
        *,
        max_bytes: int = 10 * 1024 * 1024,
        spec: VenueSpec | None = None,
        route_kind: str | None = None,
    ) -> HttpResponse:
        def fetch(target: str) -> HttpResponse:
            hostname = urlparse(target).hostname
            allowed_hosts = {hostname} if hostname else None
            return self.client.get(target, min_interval=0.2, max_bytes=max_bytes, allowed_hosts=allowed_hosts)

        hinted = self._route_hint(
            spec,
            route_kind=route_kind,
            source="official",
            fallback_url=url,
        )
        if hinted and hinted != url:
            try:
                return fetch(hinted)
            except Exception as hinted_error:
                # A catalog row is only an index hint.  A stale or temporarily
                # blocked hint must never make baseline discovery dependent on
                # the runtime database.
                try:
                    return fetch(url)
                except Exception as fallback_error:
                    raise fallback_error from hinted_error
        return fetch(url)

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
        records_scanned: int | None = None,
        records_filtered: int = 0,
        raw_scan_cap: int | None = None,
    ) -> dict[str, Any]:
        status = "error" if errors and not parsed else "partial" if errors or incomplete else "ok"
        request_urls = sorted(set(urls))
        records_incomplete = len(incomplete)
        scanned = parsed + records_incomplete if records_scanned is None else max(int(records_scanned), 0)
        requests_attempted = len(request_urls)
        requests_succeeded = min(max(int(fetched), 0), requests_attempted)
        report = {
            "source": "official",
            "adapter": self.adapter,
            "venue_group": spec.key,
            "status": status,
            "years": years,
            # ``scanned``/``fetched`` remain compatibility fields.  They now
            # describe records and successful requests respectively; the
            # explicit request/record fields remove any ambiguity for
            # observability and replay.
            "scanned": scanned,
            "fetched": requests_succeeded,
            "parsed": parsed,
            "filtered": max(int(records_filtered), 0),
            "truncated": bool(truncated),
            "budget_exhausted": bool(budget_exhausted or truncated),
            "incomplete": records_incomplete,
            "records_scanned": scanned,
            "records_valid": parsed,
            "records_filtered": max(int(records_filtered), 0),
            "records_incomplete": records_incomplete,
            "requests_attempted": requests_attempted,
            "requests_succeeded": requests_succeeded,
            "requests_failed": max(requests_attempted - requests_succeeded, 0),
            "errors": errors,
            "urls": request_urls,
        }
        if raw_scan_cap is not None:
            report["raw_scan_cap"] = max(int(raw_scan_cap), 0)
        return report

    @staticmethod
    def _error(url: str, exc: Exception) -> dict[str, Any]:
        return {"url": url, "error_type": type(exc).__name__, "message": str(exc)[:300]}


def _last_path(value: str) -> str:
    path = urlparse(value).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or "unknown"


def _is_ecva_spec(spec: VenueSpec) -> bool:
    return spec.key.casefold() == "eccv" or str(spec.adapter or "").casefold() == "ecva"


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
            absolute
            for _, href in _hrefs(root)
            if (absolute := _official_link(href, "https://aclanthology.org", {"aclanthology.org"}))
            and pattern.search(urlparse(absolute).path)
        }
        return sorted(urls)

    @staticmethod
    def parse_paper(html_text: str, *, spec: VenueSpec, url: str, year: int, response: HttpResponse | None = None) -> ParsedRecord:
        root = _tree(html_text)
        title = (_meta(root, "citation_title") or [_clean((_first(root, tag="h1") or _Node()).text())])[0]
        authors = _authors(root)
        abstract = _abstract(root)
        pdf = _pdf_url(root, url) or f"{url.rstrip('/')}.pdf"
        published = next((_iso_date(value) for name in ("citation_publication_date", "citation_date", "DC.Date.issued") for value in _meta(root, name) if _iso_date(value)), None)
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
                    volume_response = self._get(volume_url, spec=spec, route_kind="index")
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
            absolute = _official_link(href, "https://proceedings.mlr.press", {"proceedings.mlr.press"})
            volume_match = re.search(r"(?:^|/)v(\d+)(?:/|$)", urlparse(absolute).path, flags=re.IGNORECASE) if absolute else None
            if not match or not volume_match:
                continue
            values.append({"year": int(match.group(1)), "volume": f"v{volume_match.group(1)}", "url": absolute})
        return sorted({(item["year"], item["volume"]): item for item in values}.values(), key=lambda item: (item["year"], item["volume"]))

    @staticmethod
    def paper_urls(html_text: str, *, volume: str) -> list[str]:
        root = _tree(html_text)
        pattern = re.compile(rf"/{re.escape(volume)}/[^/]+\.html(?:#.*)?$")
        return sorted({absolute for _, href in _hrefs(root) if pattern.search(href) and (absolute := _official_link(href, "https://proceedings.mlr.press", {"proceedings.mlr.press"}))})

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
        paper_key = re.sub(r"\.html$", "", _last_path(url), flags=re.IGNORECASE)
        return _make_paper(
            spec=spec, adapter=self_adapter(spec, "pmlr"), source_id=f"{paper_key}:{volume}",
            title=title, authors=authors, abstract=abstract, published_at=None,
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
            index_response = self._get(urls[0], spec=spec, route_kind="index")
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
    return spec.key if spec.key in {"acl", "emnlp", "cvpr", "eccv"} else fallback


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
    if _is_ecva_spec(spec):
        return ECVAAdapter.parse_paper(
            html_text,
            spec=spec,
            url=url,
            source_id=source_id,
            year=fallback_year,
            response=response,
        )
    adapter_name = str(spec.adapter or spec.key).casefold()
    if adapter_name == "ijcai":
        return IJCAIAdapter.parse_detail(
            html_text,
            spec=spec,
            url=url,
            source_id=source_id,
            year=_source_year(source_id, fallback_year),
            response=response,
        )
    if adapter_name == "usenix":
        return USENIXAdapter.parse_paper(
            html_text,
            spec=spec,
            url=url,
            year=_source_year(source_id, fallback_year),
            source_id=source_id,
            refreshed=True,
            response=response,
        )
    if adapter_name == "ndss":
        return NDSSAdapter.parse_paper(
            html_text,
            spec=spec,
            url=url,
            year=_source_year(source_id, fallback_year),
            source_id=source_id,
            refreshed=True,
            response=response,
        )
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
        (_iso_date(value) for name in ("citation_publication_date", "citation_date", "DC.Date.issued") for value in _meta(root, name) if _iso_date(value)),
        None,
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
        return sorted({absolute for _, href in _hrefs(root) if (absolute := _official_link(href, "https://proceedings.neurips.cc", {"proceedings.neurips.cc"})) and prefix in urlparse(absolute).path and "-Abstract-Conference" in href})

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
            spec=spec, adapter=self_adapter(spec, "neurips"), source_id=f"{year}:{_last_path(url).replace('-Abstract-Conference.html', '')}",
            title=title, authors=authors, abstract=abstract, published_at=None, landing_url=url,
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
                response = self._get(index_url, spec=spec, route_kind="index")
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


class ECVAAdapter(OfficialAdapter):
    """Deterministic adapter for ECCV papers published by ECVA."""

    adapter = "ecva"
    BASE_URL = "https://www.ecva.net"
    INDEX_URL = f"{BASE_URL}/papers.php"
    _DETAIL_RE = re.compile(
        r"^/papers/eccv_(?P<year>(?:19|20)\d{2})/papers_ECCV/html/"
        r"(?P<stem>[A-Za-z0-9][A-Za-z0-9._-]*_ECCV_(?P<stem_year>(?:19|20)\d{2})_paper)\.php$",
        re.IGNORECASE,
    )
    _PDF_RE = re.compile(
        r"^/papers/eccv_(?P<year>(?:19|20)\d{2})/papers_ECCV/papers/"
        r"[A-Za-z0-9][A-Za-z0-9._-]*\.pdf$",
        re.IGNORECASE,
    )

    @classmethod
    def index_url(cls, spec: VenueSpec | None = None, year: int | None = None) -> str:
        return cls.INDEX_URL

    @classmethod
    def _url_parts(cls, url: str) -> tuple[int, str] | None:
        parsed = urlparse(url)
        if (
            parsed.scheme.casefold() != "https"
            or parsed.netloc.casefold() != "www.ecva.net"
            or parsed.query
            or parsed.fragment
        ):
            return None
        match = cls._DETAIL_RE.fullmatch(parsed.path)
        if not match or match.group("year") != match.group("stem_year"):
            return None
        return int(match.group("year")), match.group("stem")

    @classmethod
    def source_id_from_url(cls, url: str, *, spec: VenueSpec | None = None, year: int | None = None) -> str:
        parts = cls._url_parts(url)
        if parts is None or (year is not None and parts[0] != int(year)):
            raise ValueError("invalid ECVA detail URL")
        return parts[1]

    @classmethod
    def paper_urls(cls, html_text: str, *, spec: VenueSpec | None = None, year: int) -> list[str]:
        if spec is not None and not _is_ecva_spec(spec):
            return []
        urls: set[str] = set()
        for _, href in _hrefs(_tree(html_text)):
            absolute = _absolute(href.split("#", 1)[0], cls.BASE_URL)
            parts = cls._url_parts(absolute)
            if parts and parts[0] == int(year):
                urls.add(absolute)
        return sorted(urls)

    @classmethod
    def _pdf_url(cls, root: _Node, base: str, *, year: int) -> str:
        value = _pdf_url(root, base)
        parsed = urlparse(value)
        if (
            parsed.scheme.casefold() != "https"
            or parsed.netloc.casefold() != "www.ecva.net"
            or parsed.query
            or parsed.fragment
        ):
            return ""
        match = cls._PDF_RE.fullmatch(parsed.path)
        if not match or int(match.group("year")) != int(year):
            return ""
        return value

    @staticmethod
    def _authors(root: _Node) -> list[str]:
        node = _first(root, node_id="authors")
        if node:
            raw = node.text().split(";", 1)[0]
            raw = re.sub(r"\s*\*+\s*", "", raw)
            values = [_clean(item) for item in re.split(r"[,;]", raw) if _clean(item)]
            if values:
                return values
        return _authors(root)

    @staticmethod
    def _doi(root: _Node) -> str | None:
        value = _doi(root)
        if value:
            return value
        allowed_hosts = frozenset({"link.springer.com", "doi.org", "dx.doi.org"})
        for _, href in _hrefs(root):
            parsed = urlparse(href)
            host = parsed.hostname.casefold().rstrip(".") if parsed.hostname else ""
            if parsed.scheme.casefold() != "https" or host not in allowed_hosts:
                continue
            match = re.search(r"(10\.\d{4,9}/[^?#\s]+)", parsed.path)
            if match:
                value = normalize_doi(match.group(1))
                if _DOI_RE.fullmatch(value):
                    return value
        return None

    @staticmethod
    def _bibtex_inline(root: _Node) -> str:
        for node in _nodes(root, class_name="bibref"):
            value = node.text().strip()
            if value.startswith("@"):
                return value
        return ""

    @classmethod
    def parse_paper(
        cls,
        html_text: str,
        *,
        spec: VenueSpec,
        url: str,
        source_id: str | None = None,
        year: int | None = None,
        response: HttpResponse | None = None,
    ) -> ParsedRecord:
        parts = cls._url_parts(url)
        expected_source_id = source_id
        source_id = parts[1] if parts else _last_path(url).removesuffix(".php")
        if parts is None or (year is not None and parts[0] != int(year)):
            return ParsedRecord(incomplete=_incomplete(
                spec,
                adapter=cls.adapter,
                source_id=source_id,
                landing_url=url,
                reason="official_detail_url_invalid",
                partial={"url": url},
            ))
        actual_year = parts[0]
        if expected_source_id is not None and expected_source_id != parts[1]:
            return ParsedRecord(incomplete=_incomplete(
                spec,
                adapter=cls.adapter,
                source_id=expected_source_id,
                landing_url=url,
                reason="official_identity_mismatch",
                partial={"canonical_source_id": parts[1]},
            ))
        root = _tree(html_text)
        title_node = _first(root, node_id="papertitle")
        title = _clean(title_node.text()) if title_node else ""
        authors = cls._authors(root)
        abstract = _abstract(root)
        if len(abstract) >= 2 and abstract[0] == abstract[-1] and abstract[0] in {"\"", "'"}:
            abstract = abstract[1:-1].strip()
        pdf = cls._pdf_url(root, url, year=actual_year)
        if not pdf:
            return ParsedRecord(incomplete=_incomplete(
                spec,
                adapter=cls.adapter,
                source_id=source_id,
                landing_url=url,
                reason="required_official_field_missing",
                missing=("pdf_url",),
                partial={"title": title, "authors": authors, "abstract": _clean(abstract)},
            ))
        source_metadata: dict[str, Any] = {
            "proceedings_year": actual_year,
            "proceedings": f"ECCV{actual_year}",
        }
        bibtex_inline = cls._bibtex_inline(root)
        if bibtex_inline:
            source_metadata["bibtex_inline"] = bibtex_inline
        return _make_paper(
            spec=spec,
            adapter=self_adapter(spec, "ecva"),
            source_id=source_id,
            title=title,
            authors=authors,
            abstract=abstract,
            published_at=None,
            landing_url=url,
            pdf_url=pdf,
            doi=cls._doi(root),
            response=response,
            source_metadata=source_metadata,
        )

    def discover(self, plan: SearchPlan, spec: VenueSpec) -> DiscoveryResult:
        years = _years_for_plan(plan)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        index_url = self.index_url(spec)
        urls = [index_url]
        fetched = 0
        try:
            response = self._get(index_url, spec=spec, route_kind="index")
            fetched += 1
            index_html = response.text()
        except Exception as exc:
            errors.append(self._error(index_url, exc))
            index_html = ""
        for year in years:
            for paper_url in self.paper_urls(index_html, spec=spec, year=year):
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
            if len(papers) >= plan.max_results_per_venue:
                break
        return DiscoveryResult(papers, incomplete, [self._report(spec, years=years, fetched=fetched, parsed=len(papers), incomplete=incomplete, errors=errors, urls=urls, truncated=len(papers) >= plan.max_results_per_venue)])


class CVFAdapter(OfficialAdapter):
    adapter = "cvf"

    @staticmethod
    def proceedings_code(spec: VenueSpec, year: int) -> str:
        return f"{spec.key.upper()}{int(year)}"

    @classmethod
    def index_url(cls, spec: VenueSpec, year: int) -> str:
        if _is_ecva_spec(spec):
            return ECVAAdapter.index_url(spec, year)
        return f"https://openaccess.thecvf.com/{cls.proceedings_code(spec, year)}?day=all"

    @classmethod
    def source_id_from_url(cls, url: str, *, spec: VenueSpec, year: int) -> str:
        if _is_ecva_spec(spec):
            return ECVAAdapter.source_id_from_url(url, spec=spec, year=year)
        code = cls.proceedings_code(spec, year)
        path = urlparse(url).path
        match = re.search(rf"/content/{re.escape(code)}/html/([^/]+)\.html$", path, flags=re.IGNORECASE)
        return match.group(1) if match else _last_path(url).removesuffix(".html")

    @classmethod
    def paper_urls(cls, html_text: str, *, spec: VenueSpec, year: int) -> list[str]:
        if _is_ecva_spec(spec):
            return ECVAAdapter.paper_urls(html_text, spec=spec, year=year)
        code = cls.proceedings_code(spec, year)
        pattern = re.compile(rf"/content/{re.escape(code)}/html/[^/]+\.html$", re.IGNORECASE)
        base_url = "https://openaccess.thecvf.com"
        urls: set[str] = set()
        for _, href in _hrefs(_tree(html_text)):
            absolute = _absolute(href.split("#", 1)[0], base_url)
            parsed = urlparse(absolute)
            host = parsed.hostname.casefold().rstrip(".") if parsed.hostname else ""
            if parsed.scheme.casefold() != "https" or host != "openaccess.thecvf.com":
                continue
            if pattern.search(parsed.path):
                urls.add(absolute)
        return sorted(urls)

    @classmethod
    def parse_paper(
        cls,
        html_text: str,
        *,
        spec: VenueSpec,
        url: str,
        year: int,
        response: HttpResponse | None = None,
    ) -> ParsedRecord:
        if _is_ecva_spec(spec):
            return ECVAAdapter.parse_paper(html_text, spec=spec, url=url, year=year, response=response)
        root = _tree(html_text)
        title = (_meta(root, "citation_title") or [_clean((_first(root, tag="h1") or _Node()).text())])[0]
        authors = _authors(root)
        abstract = _abstract(root)
        pdf = _pdf_url(root, url)
        source_metadata: dict[str, Any] = {"proceedings_year": year, "proceedings": cls.proceedings_code(spec, year)}
        bibtex_url = _bibtex_url(root, url)
        bibtex_inline = _bibtex_inline(root)
        if bibtex_url:
            source_metadata["bibtex_url"] = bibtex_url
        if bibtex_inline:
            source_metadata["bibtex_inline"] = bibtex_inline
        published = next(
            (_iso_date(value) for name in ("citation_publication_date", "citation_date", "DC.Date.issued") for value in _meta(root, name) if _iso_date(value)),
            None,
        )
        return _make_paper(
            spec=spec,
            adapter=self_adapter(spec, "cvf"),
            source_id=f"{year}:{cls.source_id_from_url(url, spec=spec, year=year)}",
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

    def discover(self, plan: SearchPlan, spec: VenueSpec) -> DiscoveryResult:
        if _is_ecva_spec(spec):
            return ECVAAdapter(self.client, route_catalog=self.route_catalog).discover(plan, spec)
        years = _years_for_plan(plan)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        urls: list[str] = []
        fetched = 0
        for year in years:
            index_url = self.index_url(spec, year)
            urls.append(index_url)
            try:
                response = self._get(index_url, spec=spec, route_kind="index")
                fetched += 1
                paper_urls = self.paper_urls(response.text(), spec=spec, year=year)
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
            absolute = _official_link(href, base_url or cls.ARCHIVE_URL, {"ojs.aaai.org"}) if href else ""
            if year is not None and absolute:
                values.append({"title": title, "year": year, "url": absolute})
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
                    absolute = _official_link(href, base_url, {"ojs.aaai.org"})
                    if absolute:
                        return absolute
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
            article_url = _official_link(href, AAAIOJSAdapter.ARCHIVE_URL, {"ojs.aaai.org"})
            if not article_url:
                continue
            pdf = ""
            for label, link in _hrefs(node):
                if "pdf" in label.casefold() or "pdf" in link.casefold():
                    pdf = _official_link(link, article_url, {"ojs.aaai.org"})
                    if pdf:
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
            authors=authors, abstract=abstract, published_at=None,
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
                response = self._get(archive_url, spec=spec, route_kind="index")
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
    def parse_detail(
        html_text: str,
        *,
        spec: VenueSpec,
        url: str,
        source_id: str | None = None,
        year: int | None = None,
        response: HttpResponse | None = None,
    ) -> ParsedRecord:
        root = _tree(html_text)
        title = (_meta(root, "citation_title") or [
            _clean((_first(root, class_name="page-title") or _first(root, tag="h1") or _Node()).text())
        ])[0]
        authors = _authors(root)
        if not authors:
            authors_node = _first(root, tag="h2")
            authors = _author_list(authors_node.text()) if authors_node else []
        abstract = _abstract(root)
        if not abstract:
            detail = _first(root, class_name="proceedings-detail")
            for node in _nodes(detail, class_name="col-md-12") if detail else []:
                value = _clean(node.text())
                if value and not re.match(r"^keywords\s*:", value, flags=re.IGNORECASE):
                    abstract = value
                    break
        pdf = _pdf_url(root, url)
        match = re.search(r"/proceedings/((?:19|20)\d{2})/(\d+)/?$", url)
        paper_num = match.group(2) if match else ""
        year = year or (int(match.group(1)) if match else None)
        if not pdf and paper_num and year:
            pdf = f"https://www.ijcai.org/proceedings/{year}/{int(paper_num):04d}.pdf"
        resolved_source_id = source_id or (f"{year}-{paper_num}" if year and paper_num else "")
        source_metadata: dict[str, Any] = {"refreshed_from_detail": True}
        if year:
            source_metadata["proceedings_year"] = year
        if paper_num:
            source_metadata["paper_number"] = paper_num
        bibtex_url = _bibtex_url(root, url)
        if bibtex_url:
            source_metadata["bibtex_url"] = bibtex_url
        elif paper_num and year:
            source_metadata["bibtex_url"] = f"https://www.ijcai.org/proceedings/{year}/bibtex/{int(paper_num)}"
        return _make_paper(
            spec=spec,
            adapter=self_adapter(spec, "ijcai"),
            source_id=resolved_source_id,
            title=title,
            authors=authors,
            abstract=abstract,
            published_at=next(
                (_iso_date(value) for name in ("citation_publication_date", "citation_date", "DC.Date.issued") for value in _meta(root, name) if _iso_date(value)),
                None,
            ),
            landing_url=url,
            pdf_url=pdf,
            doi=_doi(root),
            response=response,
            source_metadata=source_metadata,
        )

    @staticmethod
    def parse_papers(
        html_text: str,
        *,
        spec: VenueSpec,
        year: int,
        base_url: str,
        detail_loader: Callable[[str], tuple[str, HttpResponse | None]] | None = None,
        max_records: int | None = None,
    ) -> list[ParsedRecord]:
        root = _tree(html_text)
        records: list[ParsedRecord] = []
        for node in _nodes(root, class_name="paper_wrapper"):
            if max_records is not None and len(records) >= max_records:
                break
            title_node = _first(node, class_name="title")
            authors_node = _first(node, class_name="authors")
            title = title_node.text() if title_node else ""
            authors = _author_list(authors_node.text()) if authors_node else []
            pdf = next((link for label, link in _hrefs(node) if link.casefold().endswith(".pdf")), "")
            landing = next((link for _label, link in _hrefs(node) if re.search(r"/proceedings/\d{4}/\d+/?$", link)), "")
            pdf_url = _official_link(pdf, base_url, {"www.ijcai.org"}) if pdf else ""
            landing_url = _official_link(landing, base_url, {"www.ijcai.org"}) if landing else ""
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
                    detail_record = IJCAIAdapter.parse_detail(
                        detail_html,
                        spec=spec,
                        url=landing_url,
                        source_id=f"{year}-{paper_num or 'unknown'}",
                        year=year,
                        response=response,
                    )
                    records.append(detail_record)
                    continue
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
                published_at=None,
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
                response = self._get(list_url, spec=spec, route_kind="index")
                fetched += 1
                def load_detail(url: str) -> tuple[str, HttpResponse | None]:
                    nonlocal fetched
                    detail_response = self._get(url)
                    fetched += 1
                    return detail_response.text(), detail_response
                records = self.parse_papers(
                    response.text(),
                    spec=spec,
                    year=year,
                    base_url=list_url,
                    detail_loader=load_detail,
                    max_records=plan.max_results_per_venue - len(papers) - len(incomplete),
                )
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
        return sorted({absolute for _, href in _hrefs(_tree(html_text)) if (absolute := _official_link(href, base_url, {"www.usenix.org"})) and prefix in urlparse(absolute).path})

    @staticmethod
    def _people_text(root: _Node) -> str:
        node = _first(root, class_name="field-name-field-paper-people-text")
        paragraph = _first(node, tag="p") if node else None
        if not paragraph:
            return ""
        # The affiliation is marked up as ``<em>`` on USENIX pages. Remove
        # that node before splitting the remaining author label.
        values: list[str] = []
        for child in paragraph.children:
            if isinstance(child, str):
                values.append(child)
            elif child.tag != "em":
                values.append(child.text())
        return _clean("".join(values)).rstrip(" ,")

    @staticmethod
    def _abstract_text(root: _Node) -> str:
        node = _first(root, class_name="field-name-field-paper-description")
        if not node:
            return ""
        values: list[str] = []
        for paragraph in _nodes(node, tag="p"):
            value = _clean(paragraph.text())
            if value and value not in values:
                values.append(value)
        return " ".join(values)

    @staticmethod
    def parse_paper(
        html_text: str,
        *,
        spec: VenueSpec,
        url: str,
        year: int | None,
        source_id: str | None = None,
        refreshed: bool = False,
        response: HttpResponse | None = None,
    ) -> ParsedRecord:
        root = _tree(html_text)
        title = (_meta(root, "citation_title") or [_clean((_first(root, tag="h1") or _Node()).text())])[0]
        authors = _authors(root)
        if not authors:
            authors = _author_list(USENIXAdapter._people_text(root))
        abstract = USENIXAdapter._abstract_text(root) or _abstract(root)
        pdf = _pdf_url(root, url)
        source_metadata = {"conference_year": year} if year else {}
        if refreshed:
            source_metadata["refreshed_from_detail"] = True
        bibtex_url = _bibtex_url(root, url)
        bibtex_inline = _bibtex_inline(root)
        if bibtex_url:
            source_metadata["bibtex_url"] = bibtex_url
        if bibtex_inline:
            source_metadata["bibtex_inline"] = bibtex_inline
        return _make_paper(
            spec=spec,
            adapter=self_adapter(spec, "usenix"),
            source_id=source_id or f"{year}:{_last_path(url)}",
            title=title,
            authors=authors,
            abstract=abstract,
            published_at=_iso_date(next(iter(_meta(root, "citation_publication_date")), "")),
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
                response = self._get(list_url, spec=spec, route_kind="index")
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
        return sorted({absolute for _, href in _hrefs(_tree(html_text)) if "/ndss-paper/" in href and (absolute := _official_link(href, base_url, {"www.ndss-symposium.org"}))})

    @staticmethod
    def _paper_data_paragraphs(root: _Node) -> list[str]:
        node = _first(root, class_name="paper-data")
        if not node:
            return []
        values: list[str] = []
        for paragraph in _nodes(node, tag="p"):
            value = _clean(paragraph.text())
            if value and value not in values:
                values.append(value)
        return values

    @staticmethod
    def parse_paper(
        html_text: str,
        *,
        spec: VenueSpec,
        url: str,
        year: int | None,
        source_id: str | None = None,
        refreshed: bool = False,
        response: HttpResponse | None = None,
    ) -> ParsedRecord:
        root = _tree(html_text)
        title = (_meta(root, "citation_title") or [_clean((_first(root, tag="h1") or _Node()).text())])[0]
        authors = _authors(root)
        if not authors:
            paragraphs = NDSSAdapter._paper_data_paragraphs(root)
            if paragraphs:
                authors = [
                    re.sub(r"\s*\([^)]*\)\s*$", "", item).strip()
                    for item in _split_top_level(paragraphs[0])
                    if _clean(re.sub(r"\s*\([^)]*\)\s*$", "", item))
                ]
        paragraphs = NDSSAdapter._paper_data_paragraphs(root)
        abstract = paragraphs[1] if len(paragraphs) > 1 else _abstract(root)
        pdf = _pdf_url(root, url)
        source_metadata = {"symposium_year": year} if year else {}
        if refreshed:
            source_metadata["refreshed_from_detail"] = True
        return _make_paper(
            spec=spec,
            adapter=self_adapter(spec, "ndss"),
            source_id=source_id or f"{year}:{_last_path(url)}",
            title=title,
            authors=authors,
            abstract=abstract,
            published_at=None,
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
            list_url = f"https://www.ndss-symposium.org/ndss{year}/accepted-papers/"
            urls.append(list_url)
            try:
                response = self._get(list_url, spec=spec, route_kind="index")
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


class IEEEComputerCSDLAdapter(OfficialAdapter):
    """Anonymous IEEE Computer Society CSDL adapter for IEEE S&P proceedings.

    CSDL article pages are an Angular shell, so discovery and refresh use the
    public GraphQL metadata endpoint.  Discovery accepts only authoritative
    article metadata and a fixed, source-derived PDF route; materialization
    downloads and identity-checks the PDF bytes.
    """

    adapter = "ieee_csdl"
    GROUP_ID = "1000646"
    GRAPHQL_URL = "https://www.computer.org/csdl/api/v1/graphql"
    PDF_URL_TEMPLATE = "https://www.computer.org/csdl/pds/api/csdl/proceedings/download-article/{article_id}/pdf"
    GRAPHQL_HOSTS = frozenset({"www.computer.org"})
    PDF_HOSTS = frozenset({"www.computer.org", "csdl-downloads.ieeecomputer.org"})
    # CSDL proceedings can place front matter before and between article
    # records.  Keep raw enumeration bounded independently of the requested
    # candidate count; the cap protects a malformed or unexpectedly large TOC.
    RAW_SCAN_CAP = 1000
    ARTICLE_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
    PREFIX_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*\Z")
    # CSDL uses two verified proceedings FNO forms: a purely numeric article
    # number in older years, or a numeric prefix, document-class letter, and
    # numeric suffix. ``z`` and older Roman-numeral suffixes are front matter;
    # article classes such as ``a`` and ``b`` remain eligible for detail
    # validation.
    FNO_FRONT_MATTER_RE = re.compile(r"^[0-9]+(?:z[0-9]+|[ivxlcdm]+)$", re.IGNORECASE)
    FNO_PAPER_RE = re.compile(r"^(?:[0-9]+|[0-9]+[a-y][0-9]+)$", re.IGNORECASE)
    SOURCE_ID_RE = re.compile(
        r"((?:19|20)\d{2}):(sp):((?:[0-9]+|[0-9]+[a-y][0-9]+)):([A-Za-z0-9][A-Za-z0-9._-]+)\Z"
    )
    PROCEEDINGS_QUERY = """
      query ($groupId: String) {
        proceedings(groupId: $groupId) {
          id acronym title volume displayVolume year
        }
      }
    """
    TOC_QUERY = """
      query ($proceedingId: String!, $limitResults: Int, $skipResults: Int) {
        proceeding: proceeding(proceedingId: $proceedingId) {
          id groupId acronym issn isbn startDate endDate location website
          title volume displayVolume year
        }
        articlesByProceeding: articlesByProceedingWithPagination(
          proceedingId: $proceedingId
          limit: $limitResults
          skip: $skipResults
        ) {
          limit skipped totalResults
          articleResults {
            id pubType pubDate doi idPrefix
            authors { fullName givenName surname }
            fno isOpenAccess issueNum pages sectionTitle title year
          }
        }
      }
    """
    ARTICLE_QUERY = """
      query ($articleId: String!) {
        proceeding: proceedingByArticleId(articleId: $articleId) {
          id title acronym groupId volume displayVolume year
        }
        article: articleById(articleId: $articleId) {
          id doi title normalizedTitle abstract
          abstracts { abstractType content }
          normalizedAbstract fno
          authors { affiliation fullName givenName surname }
          idPrefix isOpenAccess showRecommendedArticles showBuyMe hasPdf
          pubDate pubType pages year issn isbn notes notesType amsId
        }
      }
    """

    @staticmethod
    def _component(value: Any, *, prefix: bool = False) -> str:
        text = _clean(value)
        pattern = IEEEComputerCSDLAdapter.PREFIX_RE if prefix else IEEEComputerCSDLAdapter.ARTICLE_COMPONENT_RE
        return text if pattern.fullmatch(text) else ""

    @classmethod
    def parse_source_id(cls, source_id: str) -> tuple[int, str, str, str]:
        match = cls.SOURCE_ID_RE.fullmatch(str(source_id or "").strip())
        if not match:
            raise ValueError("invalid IEEE CSDL source id")
        return int(match.group(1)), match.group(2), match.group(3), match.group(4)

    @classmethod
    def article_url(cls, source_id: str) -> str:
        year, prefix, fno, article_id = cls.parse_source_id(source_id)
        return f"https://www.computer.org/csdl/proceedings-article/{prefix}/{year}/{fno}/{article_id}"

    @classmethod
    def pdf_url(cls, article_id: str) -> str:
        value = cls._component(article_id)
        if not value:
            raise ValueError("invalid IEEE CSDL article id")
        return cls.PDF_URL_TEMPLATE.format(article_id=value)

    def _graphql(self, query: str, variables: dict[str, Any]) -> tuple[dict[str, Any], HttpResponse]:
        encoded = urlencode({
            "query": query,
            "variables": json.dumps(variables, separators=(",", ":"), sort_keys=True),
        })
        url = f"{self.GRAPHQL_URL}?{encoded}"
        # A verified CSDL route identifies only the public GraphQL endpoint.
        # Rebuild the current operation and variables instead of replaying a
        # stale catalog query (which could point at another proceeding or
        # pagination offset).
        target_url = url
        hinted = self._route_hint(
            get_venue_spec("ieee-sp"),
            route_kind="index",
            source="official",
            fallback_url=self.GRAPHQL_URL,
        )
        if hinted:
            hinted_parts = urlparse(hinted)
            requested_parts = urlparse(url)
            target_url = hinted_parts._replace(
                query=requested_parts.query,
                fragment="",
            ).geturl()
        try:
            response = self.client.get(
                target_url,
                min_interval=0.2,
                max_bytes=20 * 1024 * 1024,
                allowed_hosts=self.GRAPHQL_HOSTS,
            )
        except Exception as hinted_error:
            if target_url == url:
                raise
            # Route catalog hints are an optimization.  A stale hint must not
            # prevent the fixed baseline endpoint from being attempted.
            try:
                response = self.client.get(
                    url,
                    min_interval=0.2,
                    max_bytes=20 * 1024 * 1024,
                    allowed_hosts=self.GRAPHQL_HOSTS,
                )
            except Exception as fallback_error:
                raise fallback_error from hinted_error
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("IEEE CSDL GraphQL response is not an object")
        errors = payload.get("errors")
        if errors:
            raise ValueError("IEEE CSDL GraphQL response contains errors")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("IEEE CSDL GraphQL response has no data object")
        return data, response

    @staticmethod
    def _text(value: Any) -> str:
        if isinstance(value, str):
            value = re.sub(r"<[^>]+>", " ", value)
        return _clean(html.unescape(str(value or "")))

    @classmethod
    def _authors_from_article(cls, article: dict[str, Any]) -> list[str]:
        values: list[str] = []
        raw = article.get("authors")
        if not isinstance(raw, list):
            return values
        for item in raw:
            if not isinstance(item, dict):
                continue
            value = cls._text(item.get("fullName"))
            if not value:
                given = cls._text(item.get("givenName"))
                surname = cls._text(item.get("surname"))
                value = " ".join(part for part in (given, surname) if part)
            if value:
                values.append(value)
        return values

    @classmethod
    def _abstract_from_article(cls, article: dict[str, Any]) -> str:
        for value in (article.get("abstract"), article.get("normalizedAbstract")):
            text = cls._text(value)
            if text:
                return _strip_abstract_label(text)
        abstracts = article.get("abstracts")
        if isinstance(abstracts, list):
            for item in abstracts:
                if isinstance(item, dict):
                    text = cls._text(item.get("content"))
                    if text:
                        return _strip_abstract_label(text)
        return ""

    @staticmethod
    def _published_at(value: Any) -> str | None:
        text = _clean(value)
        if not text:
            return None
        if re.fullmatch(r"(?:19|20)\d{2}-\d{2}-\d{2}(?:[T ][0-9:.+-]+Z?)?", text):
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.isoformat().replace("+00:00", "Z")
            except ValueError:
                pass
        return _iso_date(text)

    @classmethod
    def _source_id_from_article(cls, article: dict[str, Any], *, fallback_year: int | None = None) -> str:
        article_id = cls._component(article.get("id"))
        prefix = cls._component(article.get("idPrefix"), prefix=True)
        fno = cls._component(article.get("fno"))
        raw_year = article.get("year") or fallback_year
        try:
            year = int(raw_year)
        except (TypeError, ValueError):
            year = 0
        if not re.fullmatch(r"(?:19|20)\d{2}", str(year)) or not prefix or not fno or not article_id:
            return ""
        source_id = f"{year}:{prefix}:{fno}:{article_id}"
        return source_id if cls.FNO_PAPER_RE.fullmatch(fno) and cls.SOURCE_ID_RE.fullmatch(source_id) else ""

    @classmethod
    def _is_front_matter(cls, article: dict[str, Any]) -> bool:
        """Identify CSDL front matter from document type and FNO grammar.

        The section code is part of CSDL's authoritative FNO, so this does
        not rely on a title or a particular article identifier.  Numeric FNOs
        used by older proceedings are intentionally retained as candidates.
        """
        if cls._text(article.get("pubType")).casefold() != "proceedings":
            return False
        return bool(cls.FNO_FRONT_MATTER_RE.fullmatch(cls._text(article.get("fno"))))

    @classmethod
    def _pdf_verification(cls, response: HttpResponse) -> dict[str, Any]:
        final_url = response.final_url or response.url
        parsed = urlparse(final_url)
        content_type = ""
        for key, value in response.headers.items():
            if key.casefold() == "content-type":
                content_type = str(value)
                break
        return {
            "source_url": response.url,
            "status": response.status,
            "host": (parsed.hostname or "").casefold().rstrip("."),
            "content_type": content_type,
            "response_sha256": response.sha256,
            # CSDL redirects to a short-lived signed download URL.  Do not
            # persist that URL or its query string in candidates/provenance.
            "redirect_count": len(response.redirect_chain),
            "verified_pdf": (
                200 <= response.status < 300
                and parsed.scheme.casefold() == "https"
                and (parsed.hostname or "").casefold().rstrip(".") in cls.PDF_HOSTS
                and response.body.startswith(b"%PDF-")
            ),
        }

    def _verify_pdf(self, article_id: str) -> tuple[str, HttpResponse, dict[str, Any]]:
        fixed_url = self.pdf_url(article_id)
        response = self.client.get(
            fixed_url,
            min_interval=0.2,
            max_bytes=25 * 1024 * 1024,
            allowed_hosts=self.PDF_HOSTS,
        )
        verification = self._pdf_verification(response)
        if not verification["verified_pdf"]:
            raise ValueError("official_pdf_access_gated")
        return fixed_url, response, verification

    def _record_from_detail(
        self,
        *,
        spec: VenueSpec,
        article: dict[str, Any] | None,
        proceeding: dict[str, Any] | None,
        response: HttpResponse,
        expected_source_id: str | None = None,
        expected_proceeding_id: str | None = None,
    ) -> ParsedRecord:
        article = article if isinstance(article, dict) else {}
        proceeding = proceeding if isinstance(proceeding, dict) else {}
        fallback_year = int(proceeding.get("year")) if str(proceeding.get("year", "")).isdigit() else None
        source_id = self._source_id_from_article(article, fallback_year=fallback_year)
        article_id = self._component(article.get("id"))
        if not source_id or (expected_source_id and source_id != expected_source_id):
            return ParsedRecord(incomplete=_incomplete(
                spec,
                adapter=self.adapter,
                source_id=expected_source_id or source_id or article_id or "unknown",
                landing_url=self.article_url(expected_source_id) if expected_source_id else "https://www.computer.org/csdl/proceedings",
                reason="official_identity_mismatch",
                missing=["source_id"],
                partial={"article_id": article_id, "source_id": source_id},
            ))
        source_year, _source_prefix, _source_fno, _source_article_id = self.parse_source_id(source_id)
        landing_url = self.article_url(source_id)
        proceeding_id = self._component(proceeding.get("id"))
        proceeding_group_id = self._text(proceeding.get("groupId"))
        proceeding_acronym = self._text(proceeding.get("acronym")).casefold()
        proceeding_year = proceeding.get("year")
        if (
            not proceeding_id
            or proceeding_group_id != self.GROUP_ID
            or proceeding_acronym != "sp"
            or str(proceeding_year) != str(source_year)
            or (expected_proceeding_id is not None and proceeding_id != expected_proceeding_id)
        ):
            return ParsedRecord(incomplete=_incomplete(
                spec, adapter=self.adapter, source_id=source_id, landing_url=landing_url,
                reason="official_identity_mismatch", missing=["source_id"],
                partial={
                    "proceeding_id": proceeding_id,
                    "proceeding_group_id": proceeding_group_id,
                    "proceeding_acronym": proceeding_acronym,
                    "proceeding_year": proceeding_year,
                },
            ))
        if self._text(article.get("pubType")).casefold() != "proceedings":
            return ParsedRecord(incomplete=_incomplete(
                spec, adapter=self.adapter, source_id=source_id, landing_url=landing_url,
                reason="official_document_type_invalid", missing=["publication_type"],
                partial={"pub_type": self._text(article.get("pubType"))},
            ))
        if article.get("isOpenAccess") is not True:
            return ParsedRecord(incomplete=_incomplete(
                spec, adapter=self.adapter, source_id=source_id, landing_url=landing_url,
                reason="official_pdf_access_gated", missing=["pdf_url"],
                partial={"title": self._text(article.get("title")), "isOpenAccess": article.get("isOpenAccess"), "hasPdf": article.get("hasPdf")},
            ))
        if article.get("hasPdf") is not True:
            return ParsedRecord(incomplete=_incomplete(
                spec, adapter=self.adapter, source_id=source_id, landing_url=landing_url,
                reason="official_pdf_unavailable", missing=["pdf_url"],
                partial={"title": self._text(article.get("title")), "isOpenAccess": article.get("isOpenAccess"), "hasPdf": article.get("hasPdf")},
            ))
        # Discovery stays metadata-only.  Materialization fetches this fixed
        # URL with the registered redirect hosts, validates the PDF signature,
        # and checks its first pages against the refreshed title.
        fixed_pdf_url = self.pdf_url(article_id)
        doi = normalize_doi(self._text(article.get("doi")))
        if doi and not _DOI_RE.fullmatch(doi):
            doi = None
        if not doi:
            return ParsedRecord(incomplete=_incomplete(
                spec, adapter=self.adapter, source_id=source_id, landing_url=landing_url,
                reason="official_doi_missing", missing=["doi", "bibtex"],
                partial={"title": self._text(article.get("title"))},
            ))
        source_metadata = {
            "csdl_group_id": proceeding_group_id,
            "proceeding_id": proceeding_id,
            "id_prefix": self._text(article.get("idPrefix")),
            "fno": self._text(article.get("fno")),
            "is_open_access": True,
            "has_pdf": True,
            "pages": self._text(article.get("pages")),
            "pub_type": self._text(article.get("pubType")),
            # DOI content negotiation remains the authoritative BibTeX route
            # for normal IEEE papers.  Keep the fixed CSDL route available to
            # callers even when a record has no DOI.
            "pdf_url": fixed_pdf_url,
        }
        return _make_paper(
            spec=spec,
            adapter=self.adapter,
            source_id=source_id,
            title=self._text(article.get("title")),
            authors=self._authors_from_article(article),
            abstract=self._abstract_from_article(article),
            published_at=self._published_at(article.get("pubDate")),
            landing_url=landing_url,
            pdf_url=fixed_pdf_url,
            doi=doi,
            response=response,
            source_metadata=source_metadata,
        )

    def fetch_by_id(self, paper: PaperFacts, spec: VenueSpec) -> PaperFacts:
        expected_year, expected_prefix, expected_fno, expected_article_id = self.parse_source_id(paper.source_id)
        data, response = self._graphql(self.ARTICLE_QUERY, {"articleId": expected_article_id})
        record = self._record_from_detail(
            spec=spec,
            article=data.get("article"),
            proceeding=data.get("proceeding"),
            response=response,
            expected_source_id=paper.source_id,
        )
        if record.paper is None:
            reason = (record.incomplete or {}).get("reason", "official identity lookup failed")
            raise ValueError(str(reason))
        # The source-id parser above is deliberately repeated after the
        # GraphQL response so a detail response cannot silently change route
        # identity during refresh.
        if (expected_year, expected_prefix, expected_fno, expected_article_id) != self.parse_source_id(record.paper.source_id):
            raise ValueError("official identity lookup returned no exact match")
        return record.paper

    def discover(self, plan: SearchPlan, spec: VenueSpec) -> DiscoveryResult:
        years = _years_for_plan(plan)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        urls: list[str] = []
        fetched = 0
        scanned = 0
        filtered = 0
        try:
            data, response = self._graphql(self.PROCEEDINGS_QUERY, {"groupId": self.GROUP_ID})
            fetched += 1
            urls.append(response.url)
            rows = data.get("proceedings")
            if not isinstance(rows, list):
                raise ValueError("IEEE CSDL proceedings response is not a list")
        except Exception as exc:
            errors.append(self._error(self.GRAPHQL_URL, exc))
            return DiscoveryResult([], [], [self._report(spec, years=years, fetched=fetched, parsed=0, incomplete=[], errors=errors, urls=urls)])
        proceedings = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                year = int(row.get("year"))
            except (TypeError, ValueError):
                continue
            title = self._text(row.get("title"))
            acronym = self._text(row.get("acronym")).casefold()
            if year in years and acronym == "sp" and "security and privacy" in title.casefold():
                proceeding_id = self._component(row.get("id"))
                if proceeding_id:
                    proceedings.append((year, proceeding_id))
        page_size = min(plan.max_results_per_query, 100, plan.max_results_per_venue)
        for year, proceeding_id in sorted(set(proceedings)):
            offset = 0
            while scanned < self.RAW_SCAN_CAP and len(papers) < plan.max_results_per_venue:
                limit = min(page_size, self.RAW_SCAN_CAP - scanned)
                try:
                    data, response = self._graphql(self.TOC_QUERY, {
                        "proceedingId": proceeding_id,
                        "limitResults": limit,
                        "skipResults": offset,
                    })
                    fetched += 1
                    urls.append(response.url)
                    block = data.get("articlesByProceeding")
                    if not isinstance(block, dict) or not isinstance(block.get("articleResults"), list):
                        raise ValueError("IEEE CSDL articles response is malformed")
                    articles = block["articleResults"]
                    total = int(block.get("totalResults") or 0)
                except Exception as exc:
                    errors.append(self._error(self.GRAPHQL_URL, exc))
                    break
                if not articles:
                    break
                for summary in articles:
                    if scanned >= self.RAW_SCAN_CAP or len(papers) >= plan.max_results_per_venue:
                        break
                    scanned += 1
                    if not isinstance(summary, dict):
                        incomplete.append(_incomplete(
                            spec, adapter=self.adapter, source_id=f"{year}:sp:unknown:record-{scanned}",
                            landing_url=f"https://www.computer.org/csdl/proceedings/{self.GROUP_ID}",
                            reason="official_metadata_malformed", missing=["title", "authors", "abstract", "pdf_url"],
                        ))
                        continue
                    if self._is_front_matter(summary):
                        filtered += 1
                        continue
                    article_id = self._component(summary.get("id"))
                    try:
                        detail_data, detail_response = self._graphql(self.ARTICLE_QUERY, {"articleId": article_id})
                        fetched += 1
                        urls.append(detail_response.url)
                        record = self._record_from_detail(
                            spec=spec,
                            article=detail_data.get("article"),
                            proceeding=detail_data.get("proceeding"),
                            response=detail_response,
                            expected_source_id=self._source_id_from_article(summary, fallback_year=year) or None,
                            expected_proceeding_id=proceeding_id,
                        )
                        if record.paper:
                            papers.append(record.paper)
                        elif record.incomplete:
                            incomplete.append(record.incomplete)
                    except Exception as exc:
                        incomplete.append(_incomplete(
                            spec, adapter=self.adapter,
                            source_id=f"{year}:sp:unknown:{article_id or f'record-{scanned}'}",
                            landing_url=(f"https://www.computer.org/csdl/proceedings-article/sp/{year}/unknown/{article_id}" if article_id else f"https://www.computer.org/csdl/proceedings/{proceeding_id}"),
                            reason="official_detail_fetch_or_parse_failed",
                            partial={"error_type": type(exc).__name__, "message": str(exc)[:200]},
                        ))
                offset += len(articles)
                if offset >= total or len(articles) < limit:
                    break
        return DiscoveryResult(
            papers,
            incomplete,
            [self._report(
                spec,
                years=years,
                fetched=fetched,
                parsed=len(papers),
                incomplete=incomplete,
                errors=errors,
                urls=urls,
                truncated=scanned >= self.RAW_SCAN_CAP or len(papers) >= plan.max_results_per_venue,
                budget_exhausted=scanned >= self.RAW_SCAN_CAP or len(papers) >= plan.max_results_per_venue,
                records_scanned=scanned,
                records_filtered=filtered,
                raw_scan_cap=self.RAW_SCAN_CAP,
            )],
        )


ADAPTERS: dict[str, type[OfficialAdapter]] = {
    "acl_anthology": ACLAnthologyAdapter,
    "pmlr": PMLRAdapter,
    "neurips": NeurIPSAdapter,
    "cvf": CVFAdapter,
    "ecva": ECVAAdapter,
    "aaai_ojs": AAAIOJSAdapter,
    "ijcai": IJCAIAdapter,
    "usenix": USENIXAdapter,
    "ndss": NDSSAdapter,
    "ieee_csdl": IEEEComputerCSDLAdapter,
}
