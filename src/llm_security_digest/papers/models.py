from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


FACT_FIELDS = {
    "title",
    "authors",
    "abstract",
    "publication_status",
    "venue",
    "published_at",
    "updated_at",
    "doi",
    "landing_url",
    "pdf_url",
    "bibtex",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_title(value: str) -> str:
    # BibTeX commonly wraps capitalization in braces and encodes accents as
    # LaTeX commands. Remove formatting commands before identity comparison.
    value = value or ""
    for _ in range(3):
        value = re.sub(r"\\(?:text[a-zA-Z]+|emph|mathrm|mathbf|mathit)\s*\{([^{}]*)\}", r"\1", value)
    # BibTeX uses commands such as {\\\"U}ber for Unicode characters. Reduce
    # both the source form and the LaTeX form to the same base characters.
    value = re.sub(
        r"\\(?P<accent>[\"'`^~=\.uvHc])\s*\{?(?P<char>[A-Za-z])\}?",
        lambda match: match.group("char"),
        value,
    )
    value = re.sub(r"\\[a-zA-Z]+\*?", "", value)
    value = value.replace("{", "").replace("}", "").replace("~", " ")
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    # Keep non-Latin titles (for example Chinese or Greek) in the identity
    # key; restricting this to [a-z0-9] silently rejects valid papers.
    return "".join(char for char in value if char.isalnum())


def normalize_doi(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    return value.rstrip(". ")


@dataclass
class PaperFacts:
    paper_id: str
    source: str
    source_id: str
    title: str
    authors: list[str]
    abstract: str
    publication_status: str
    venue: str | None
    published_at: str | None
    updated_at: str | None
    doi: str | None
    landing_url: str
    pdf_url: str
    primary_category: str | None = None
    categories: list[str] = field(default_factory=list)
    source_comment: str | None = None
    bibtex: str | None = None
    bibtex_url: str | None = None
    platform_links: dict[str, str] = field(default_factory=dict)
    scholar: dict[str, Any] = field(default_factory=dict)
    content: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)

    def validate_discovered(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*:.+", self.paper_id):
            raise ValueError(f"invalid paper_id: {self.paper_id!r}")
        if not self.title.strip() or not normalize_title(self.title):
            raise ValueError(f"{self.paper_id}: missing title")
        if not self.authors or not all(str(author).strip() for author in self.authors):
            raise ValueError(f"{self.paper_id}: missing authors")
        if not self.abstract.strip():
            raise ValueError(f"{self.paper_id}: missing abstract")
        if self.publication_status not in {"preprint", "accepted", "published"}:
            raise ValueError(f"{self.paper_id}: invalid publication status")
        if self.publication_status != "preprint" and not self.venue:
            raise ValueError(f"{self.paper_id}: accepted/published paper has no venue")
        if not self.landing_url.startswith("https://") or not self.pdf_url.startswith("https://"):
            raise ValueError(f"{self.paper_id}: non-HTTPS paper URL")

    def validate_materialized(self) -> None:
        self.validate_discovered()
        if not self.bibtex or not self.bibtex_url:
            raise ValueError(f"{self.paper_id}: authoritative BibTeX unavailable")
        if not self.content.get("sha256") or not self.content.get("path"):
            raise ValueError(f"{self.paper_id}: verified full text unavailable")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PaperFacts":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        return cls(**{key: item for key, item in value.items() if key in allowed})


DEFAULT_OPENREVIEW_VENUES = [
    "ICLR.cc/2026/Conference",
    "ICLR.cc/2025/Conference",
    "NeurIPS.cc/2025/Conference",
    "ICML.cc/2026/Conference",
]


@dataclass
class SearchPlan:
    queries: list[str]
    filter_keywords: list[str] = field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None
    sources: list[str] = field(default_factory=lambda: ["arxiv", "openreview"])
    openreview_venues: list[str] = field(default_factory=lambda: list(DEFAULT_OPENREVIEW_VENUES))
    crossref_venues: list[str] = field(default_factory=list)
    max_results_per_query: int = 100
    max_results_per_venue: int = 500
    scholar_enrich_limit: int = 30
    target: int = 10

    @classmethod
    def load(cls, path: Path) -> "SearchPlan":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("search plan must be an object")
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(f"unknown search-plan fields: {', '.join(unknown)}")
        plan = cls(**raw)
        plan.validate()
        return plan

    def validate(self) -> None:
        if not self.queries or not all(isinstance(query, str) and query.strip() for query in self.queries):
            raise ValueError("at least one non-empty query is required")
        if len(self.queries) > 30 or any(len(query) > 500 for query in self.queries):
            raise ValueError("query plan exceeds safety limits")
        if len(self.filter_keywords) > 100 or any(not item.strip() or len(item) > 100 for item in self.filter_keywords):
            raise ValueError("filter_keywords exceeds safety limits")
        supported = {"arxiv", "openreview", "crossref"}
        if not self.sources:
            raise ValueError("at least one source is required")
        unknown_sources = sorted(set(self.sources) - supported)
        if unknown_sources:
            raise ValueError(f"unsupported sources: {', '.join(unknown_sources)}")
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("sources must not contain duplicates")
        if not 1 <= int(self.max_results_per_query) <= 1000:
            raise ValueError("max_results_per_query must be between 1 and 1000")
        if not 1 <= int(self.max_results_per_venue) <= 5000:
            raise ValueError("max_results_per_venue must be between 1 and 5000")
        if not 1 <= int(self.target) <= 50:
            raise ValueError("target must be between 1 and 50")
        if not 0 <= int(self.scholar_enrich_limit) <= 100:
            raise ValueError("scholar_enrich_limit must be between 0 and 100")
        parsed_dates: dict[str, date] = {}
        for field_name in ("date_from", "date_to"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"{field_name} must be an ISO date")
            try:
                parsed_dates[field_name] = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{field_name} must be an ISO date") from exc
        if parsed_dates.get("date_from") and parsed_dates.get("date_to"):
            if parsed_dates["date_from"] > parsed_dates["date_to"]:
                raise ValueError("date_from must not be later than date_to")


@dataclass
class SelectionEntry:
    paper_id: str
    score: float
    category: str
    reason: str

    @classmethod
    def load_many(cls, path: Path) -> list["SelectionEntry"]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        values = raw.get("selections") if isinstance(raw, dict) else None
        if not isinstance(values, list):
            raise ValueError("selection must contain a selections array")
        allowed = {"paper_id", "score", "category", "reason"}
        entries: list[SelectionEntry] = []
        seen: set[str] = set()
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                raise ValueError(f"selection {index} must be an object")
            unknown = set(value) - allowed
            forbidden = unknown & FACT_FIELDS
            if forbidden:
                raise ValueError(f"selection {index} contains forbidden fact fields: {sorted(forbidden)}")
            if unknown:
                raise ValueError(f"selection {index} contains unknown fields: {sorted(unknown)}")
            entry = cls(
                paper_id=str(value.get("paper_id", "")).strip(),
                score=float(value.get("score", 0)),
                category=str(value.get("category", "Other")).strip() or "Other",
                reason=str(value.get("reason", "")).strip(),
            )
            if not entry.paper_id or entry.paper_id in seen:
                raise ValueError(f"selection {index} has a missing or duplicate paper_id")
            seen.add(entry.paper_id)
            entries.append(entry)
        return entries
