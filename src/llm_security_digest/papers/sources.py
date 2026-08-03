from __future__ import annotations

import html
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib import parse

from .http import HttpClient, HttpResponse
from .models import PaperFacts, SearchPlan, normalize_doi, normalize_title, utc_now


ARXIV_API_URL = "https://export.arxiv.org/api/query"
# api2.openreview.net is the retired endpoint and now responds with a
# migration/challenge page. Keep the current public API as the only source.
OPENREVIEW_API_URL = "https://api.openreview.net/notes"
CROSSREF_API_URL = "https://api.crossref.org/works"
SERPAPI_URL = "https://serpapi.com/search.json"

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_NS = {"arxiv": "http://arxiv.org/schemas/atom"}


def _text(value: Any) -> str:
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if isinstance(value, list):
        return " ".join(_text(item) for item in value if _text(item))
    return " ".join(str(value or "").split())


def _list(value: Any) -> list[str]:
    if isinstance(value, dict) and "value" in value:
        value = value["value"]
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def _iso_from_millis(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def _strip_markup(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(html.unescape(value).split())


def _provenance(response: HttpResponse, *, source: str) -> dict[str, Any]:
    return {
        "source": source,
        "source_url": response.url,
        "fetched_at": utc_now(),
        "response_sha256": response.sha256,
        "extractor_version": "1",
    }


def platform_links(*, title: str, landing_url: str, doi: str | None = None, arxiv_id: str | None = None) -> dict[str, str]:
    encoded_title = parse.quote_plus(f'"{title}"')
    links = {
        "primary": landing_url,
        "google_scholar": f"https://scholar.google.com/scholar?q={encoded_title}",
        "semantic_scholar": f"https://www.semanticscholar.org/search?q={encoded_title}",
    }
    if doi:
        links["doi"] = f"https://doi.org/{normalize_doi(doi)}"
    if arxiv_id:
        links["arxiv"] = f"https://arxiv.org/abs/{arxiv_id}"
    return links


class ArxivSource:
    name = "arxiv"

    def __init__(self, client: HttpClient):
        self.client = client

    def discover(self, plan: SearchPlan) -> list[PaperFacts]:
        papers: list[PaperFacts] = []
        for query in plan.queries:
            params = {
                "search_query": query,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": str(plan.max_results_per_query),
            }
            response = self.client.get(
                f"{ARXIV_API_URL}?{parse.urlencode(params)}",
                min_interval=3.1,
                max_bytes=20 * 1024 * 1024,
            )
            papers.extend(self.parse_feed(response))
        return papers

    @staticmethod
    def parse_feed(response: HttpResponse) -> list[PaperFacts]:
        root = ET.fromstring(response.body)
        provenance = _provenance(response, source="arxiv_atom")
        papers: list[PaperFacts] = []
        for entry in root.findall("atom:entry", ATOM_NS):
            raw_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
            match = re.search(r"arxiv\.org/abs/([^?#]+)", raw_id)
            if not match:
                continue
            versioned_id = match.group(1).rstrip("/")
            arxiv_id = re.sub(r"v\d+$", "", versioned_id)
            title = _text(entry.findtext("atom:title", default="", namespaces=ATOM_NS))
            abstract = _text(entry.findtext("atom:summary", default="", namespaces=ATOM_NS))
            authors = [
                _text(author.findtext("atom:name", default="", namespaces=ATOM_NS))
                for author in entry.findall("atom:author", ATOM_NS)
            ]
            authors = [author for author in authors if author]
            primary = entry.find("arxiv:primary_category", ARXIV_NS)
            categories = [item.get("term", "") for item in entry.findall("atom:category", ATOM_NS)]
            doi = normalize_doi(entry.findtext("arxiv:doi", default="", namespaces=ARXIV_NS)) or None
            comment = _text(entry.findtext("arxiv:comment", default="", namespaces=ARXIV_NS)) or None
            landing_url = f"https://arxiv.org/abs/{arxiv_id}"
            paper = PaperFacts(
                paper_id=f"arxiv:{arxiv_id}",
                source="arxiv",
                source_id=arxiv_id,
                title=title,
                authors=authors,
                abstract=abstract,
                publication_status="preprint",
                venue=None,
                published_at=_text(entry.findtext("atom:published", default="", namespaces=ATOM_NS)) or None,
                updated_at=_text(entry.findtext("atom:updated", default="", namespaces=ATOM_NS)) or None,
                doi=doi,
                landing_url=landing_url,
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
                primary_category=primary.get("term") if primary is not None else None,
                categories=[category for category in categories if category],
                source_comment=comment,
                platform_links=platform_links(title=title, landing_url=landing_url, doi=doi, arxiv_id=arxiv_id),
                provenance={field: dict(provenance) for field in (
                    "title", "authors", "abstract", "published_at", "updated_at", "doi", "landing_url", "pdf_url"
                )},
            )
            paper.validate_discovered()
            papers.append(paper)
        return papers

    def fetch_by_id(self, arxiv_id: str) -> PaperFacts:
        params = {"id_list": arxiv_id, "max_results": "2"}
        response = self.client.get(
            f"{ARXIV_API_URL}?{parse.urlencode(params)}",
            min_interval=3.1,
            max_bytes=20 * 1024 * 1024,
        )
        matches = [paper for paper in self.parse_feed(response) if paper.source_id == arxiv_id]
        if len(matches) != 1:
            raise ValueError(f"arXiv identity lookup returned {len(matches)} matches for {arxiv_id}")
        return matches[0]


class OpenReviewSource:
    name = "openreview"

    def __init__(self, client: HttpClient):
        self.client = client

    def discover(self, plan: SearchPlan) -> list[PaperFacts]:
        papers: list[PaperFacts] = []
        page_size = min(plan.max_results_per_venue, 1000)
        for venue_id in plan.openreview_venues:
            offset = 0
            while offset < plan.max_results_per_venue:
                params = {
                    "content.venueid": venue_id,
                    "limit": str(min(page_size, plan.max_results_per_venue - offset)),
                    "offset": str(offset),
                    "details": "replyCount",
                }
                response = self.client.get(
                    f"{OPENREVIEW_API_URL}?{parse.urlencode(params)}",
                    min_interval=0.25,
                    max_bytes=30 * 1024 * 1024,
                )
                payload = response.json()
                notes = payload.get("notes", []) if isinstance(payload, dict) else []
                papers.extend(self.parse_notes(notes, venue_id=venue_id, response=response))
                if len(notes) < int(params["limit"]):
                    break
                offset += len(notes)
        return papers

    @staticmethod
    def parse_notes(notes: Iterable[dict[str, Any]], *, venue_id: str | None, response: HttpResponse) -> list[PaperFacts]:
        papers: list[PaperFacts] = []
        provenance = _provenance(response, source="openreview_api")
        for note in notes:
            content = note.get("content") or {}
            assigned_venue_id = _text(content.get("venueid"))
            venue_text = _text(content.get("venue"))
            lowered = venue_text.casefold()
            if venue_id and assigned_venue_id != venue_id:
                continue
            # A venue id is also present on submissions. Only records with an
            # explicit non-submission venue are eligible for the accepted
            # status; missing venue text is therefore rejected closed.
            if not venue_text or any(word in lowered for word in ("reject", "withdraw", "submitted", "submission")):
                continue
            forum_id = _text(note.get("forum") or note.get("id"))
            title = _text(content.get("title"))
            abstract = _text(content.get("abstract"))
            authors = _list(content.get("authors"))
            if not forum_id:
                continue
            landing_url = f"https://openreview.net/forum?id={parse.quote(forum_id)}"
            doi = normalize_doi(_text(content.get("doi"))) or None
            paper = PaperFacts(
                paper_id=f"openreview:{forum_id}",
                source="openreview",
                source_id=forum_id,
                title=title,
                authors=authors,
                abstract=abstract,
                publication_status="accepted",
                venue=venue_text or venue_id,
                published_at=_iso_from_millis(note.get("pdate")),
                updated_at=_iso_from_millis(note.get("mdate")),
                doi=doi,
                landing_url=landing_url,
                pdf_url=f"https://openreview.net/pdf?id={parse.quote(forum_id)}",
                categories=[venue_id],
                platform_links=platform_links(title=title, landing_url=landing_url, doi=doi),
                provenance={field: dict(provenance) for field in (
                    "title", "authors", "abstract", "publication_status", "venue", "published_at", "doi", "landing_url", "pdf_url"
                )},
            )
            try:
                paper.validate_discovered()
            except ValueError:
                continue
            papers.append(paper)
        return papers

    def fetch_by_id(self, forum_id: str) -> PaperFacts:
        response = self.client.get(
            f"{OPENREVIEW_API_URL}?{parse.urlencode({'id': forum_id})}",
            min_interval=0.25,
            max_bytes=10 * 1024 * 1024,
        )
        payload = response.json()
        notes = payload.get("notes", []) if isinstance(payload, dict) else []
        matches = [paper for paper in self.parse_notes(notes, venue_id=None, response=response) if paper.source_id == forum_id]
        if len(matches) != 1:
            raise ValueError(f"OpenReview identity lookup returned {len(matches)} matches for {forum_id}")
        return matches[0]


class CrossrefSource:
    name = "crossref"

    def __init__(self, client: HttpClient, *, contact_email: str | None = None):
        self.client = client
        self.contact_email = contact_email

    def discover(self, plan: SearchPlan) -> list[PaperFacts]:
        papers: list[PaperFacts] = []
        for venue in plan.crossref_venues:
            for query in plan.queries:
                filters = ["type:proceedings-article"]
                if plan.date_from:
                    filters.append(f"from-pub-date:{plan.date_from}")
                if plan.date_to:
                    filters.append(f"until-pub-date:{plan.date_to}")
                params = {
                    "query.bibliographic": query,
                    "query.container-title": venue,
                    "filter": ",".join(filters),
                    "rows": str(min(plan.max_results_per_query, 1000)),
                    "select": "DOI,title,author,abstract,container-title,published,URL,link,subject",
                }
                if self.contact_email:
                    params["mailto"] = self.contact_email
                response = self.client.get(
                    f"{CROSSREF_API_URL}?{parse.urlencode(params)}",
                    min_interval=0.1,
                    max_bytes=30 * 1024 * 1024,
                )
                papers.extend(self.parse_items(response, expected_venue=venue))
        return papers

    @staticmethod
    def parse_items(response: HttpResponse, *, expected_venue: str) -> list[PaperFacts]:
        payload = response.json()
        items = ((payload.get("message") or {}).get("items") or []) if isinstance(payload, dict) else []
        provenance = _provenance(response, source="crossref_api")
        papers: list[PaperFacts] = []
        expected_key = normalize_title(expected_venue)
        for item in items:
            venues = _list(item.get("container-title"))
            if expected_key and not any(expected_key in normalize_title(value) or normalize_title(value) in expected_key for value in venues):
                continue
            doi = normalize_doi(_text(item.get("DOI")))
            title = _text(item.get("title"))
            authors = []
            for author in item.get("author") or []:
                name = " ".join(part for part in (_text(author.get("given")), _text(author.get("family"))) if part)
                if name:
                    authors.append(name)
            abstract = _strip_markup(_text(item.get("abstract")))
            pdf_url = ""
            for link in item.get("link") or []:
                if "pdf" in _text(link.get("content-type")).lower() and str(link.get("URL", "")).startswith("https://"):
                    pdf_url = str(link["URL"])
                    break
            if not all((doi, title, authors, abstract, pdf_url)):
                continue
            published_parts = ((item.get("published") or {}).get("date-parts") or [[]])[0]
            published_at = "-".join(str(part).zfill(2) for part in published_parts) or None
            landing_url = f"https://doi.org/{doi}"
            paper = PaperFacts(
                paper_id=f"doi:{doi}",
                source="crossref",
                source_id=doi,
                title=title,
                authors=authors,
                abstract=abstract,
                publication_status="published",
                venue=venues[0] if venues else expected_venue,
                published_at=published_at,
                updated_at=None,
                doi=doi,
                landing_url=landing_url,
                pdf_url=pdf_url,
                categories=_list(item.get("subject")),
                platform_links=platform_links(title=title, landing_url=landing_url, doi=doi),
                provenance={field: dict(provenance) for field in (
                    "title", "authors", "abstract", "publication_status", "venue", "published_at", "doi", "landing_url", "pdf_url"
                )},
            )
            try:
                paper.validate_discovered()
            except ValueError:
                continue
            papers.append(paper)
        return papers

    def fetch_by_doi(self, doi: str, *, expected_venue: str) -> PaperFacts:
        response = self.client.get(
            f"{CROSSREF_API_URL}/{parse.quote(normalize_doi(doi), safe='')}",
            min_interval=0.1,
            max_bytes=10 * 1024 * 1024,
        )
        payload = response.json()
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict):
            raise ValueError("Crossref identity lookup returned no work")
        wrapper = HttpResponse(response.url, response.status, response.headers, json.dumps({"message": {"items": [message]}}).encode())
        matches = self.parse_items(wrapper, expected_venue=expected_venue)
        if len(matches) != 1:
            raise ValueError(f"Crossref identity lookup returned {len(matches)} matches for {doi}")
        return matches[0]


class GoogleScholarEnricher:
    def __init__(self, client: HttpClient, *, api_key: str | None = None):
        self.client = client
        self.api_key = api_key or os.getenv("SERPAPI_API_KEY")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def enrich(self, paper: PaperFacts) -> dict[str, Any]:
        if not self.api_key:
            return {"status": "unavailable", "reason": "SERPAPI_API_KEY is not configured"}
        params = {
            "engine": "google_scholar",
            "q": f'"{paper.title}"',
            "api_key": self.api_key,
            "num": "10",
        }
        redacted = f"{SERPAPI_URL}?{parse.urlencode({key: value for key, value in params.items() if key != 'api_key'})}"
        response = self.client.get(
            f"{SERPAPI_URL}?{parse.urlencode(params)}",
            min_interval=0.25,
            max_bytes=5 * 1024 * 1024,
            provenance_url=redacted,
        )
        payload = response.json()
        expected = normalize_title(paper.title)
        matches = [
            result for result in payload.get("organic_results", [])
            if normalize_title(_text(result.get("title"))) == expected
        ]
        if len(matches) != 1:
            return {
                "status": "not_matched",
                "matched_count": len(matches),
                "fetched_at": utc_now(),
                "source_url": response.url,
                "response_sha256": response.sha256,
            }
        result = matches[0]
        cited_by = ((result.get("inline_links") or {}).get("cited_by") or {})
        scholar_url = paper.platform_links.get("google_scholar") or platform_links(
            title=paper.title,
            landing_url=paper.landing_url,
            doi=paper.doi,
            arxiv_id=paper.source_id if paper.source == "arxiv" else None,
        )["google_scholar"]
        return {
            "status": "matched",
            "result_id": _text(result.get("result_id")) or None,
            "result_url": _text(result.get("link")) or scholar_url,
            "cited_by_count": cited_by.get("total"),
            "cited_by_url": _text(cited_by.get("link")) or None,
            "fetched_at": utc_now(),
            "source_url": response.url,
            "response_sha256": response.sha256,
        }
