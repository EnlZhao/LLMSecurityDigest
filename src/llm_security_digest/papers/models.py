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


def _registry_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    return "".join(char for char in value if char.isalnum())


@dataclass(frozen=True)
class VenueSpec:
    """A controlled venue definition used by official source adapters.

    Values in this registry are routing and matching hints, not paper facts.
    Crossref lookups are only allowed for a registered spec and must match an
    ISSN or a known container title.
    """

    key: str
    name: str
    aliases: tuple[str, ...] = ()
    official_urls: tuple[str, ...] = ()
    openreview_ids: tuple[str, ...] = ()
    crossref_issns: tuple[str, ...] = ()
    crossref_container_titles: tuple[str, ...] = ()
    source_kinds: tuple[str, ...] = ("official",)
    adapter: str | None = None

    @property
    def canonical_name(self) -> str:
        return self.name

    @property
    def official_sources(self) -> tuple[str, ...]:
        return self.official_urls

    @property
    def openreview_venue_ids(self) -> tuple[str, ...]:
        return self.openreview_ids

    @property
    def issns(self) -> tuple[str, ...]:
        return self.crossref_issns

    @property
    def container_titles(self) -> tuple[str, ...]:
        return self.crossref_container_titles

    def matches_name(self, value: str) -> bool:
        candidate = normalize_title(value)
        names = (self.name, self.key, *self.aliases, *self.crossref_container_titles)
        return any(candidate == normalize_title(item) for item in names if item)

    def matches_container(self, value: str) -> bool:
        candidate = normalize_title(value)
        names = self.crossref_container_titles or (self.name,)
        return any(
            candidate == normalize_title(item)
            or normalize_title(item) in candidate
            or candidate in normalize_title(item)
            for item in names if item
        )

    def matches_openreview(self, value: str) -> bool:
        """Match an OpenReview venue id, including another year in its family.

        OpenReview changes the year segment of a venue id every cycle.  The
        family and path suffix remain registry-owned, so accepting a different
        year is safe while an arbitrary ``*.cc`` family is still rejected.
        """
        candidate = str(value or "").strip().rstrip("/").casefold()
        if not candidate:
            return False
        for registered in self.openreview_ids:
            expected = str(registered).strip().rstrip("/").casefold()
            if candidate == expected:
                return True
            expected_parts = expected.split("/")
            candidate_parts = candidate.split("/")
            if len(expected_parts) != 3 or len(candidate_parts) != 3:
                continue
            if candidate_parts[0] != expected_parts[0] or candidate_parts[2] != expected_parts[2]:
                continue
            if re.fullmatch(r"(?:19|20)\d{2}", candidate_parts[1]) and re.fullmatch(
                r"(?:19|20)\d{2}", expected_parts[1]
            ):
                return True
        return False


# Keep the registry explicit and intentionally boring: adding a venue is a
# reviewable code change rather than accepting arbitrary user supplied strings.
VENUE_SPECS: tuple[VenueSpec, ...] = (
    VenueSpec("usenix-security", "USENIX Security", ("USENIX Security Symposium",),
              ("https://www.usenix.org/conference/usenixsecurity",),
              crossref_container_titles=("USENIX Security Symposium", "USENIX Security"),
              adapter="usenix"),
    VenueSpec("ieee-sp", "IEEE Symposium on Security and Privacy", ("IEEE S&P", "IEEE Symposium on Security and Privacy"),
              ("https://www.ieee-security.org/TC/SP-Index.html",),
              crossref_issns=("1081-6011",), crossref_container_titles=("IEEE Symposium on Security and Privacy", "Proceedings - IEEE Symposium on Security and Privacy"),
              source_kinds=("crossref", "ieee_xplore"), adapter="crossref"),
    VenueSpec("acm-ccs", "ACM Conference on Computer and Communications Security", ("ACM CCS", "CCS"),
              ("https://www.sigsac.org/ccs.html",),
              crossref_container_titles=("Proceedings of the ACM Conference on Computer and Communications Security", "ACM SIGSAC Conference on Computer and Communications Security", "ACM CCS"),
              source_kinds=("crossref",), adapter="crossref"),
    VenueSpec("ndss", "Network and Distributed System Security Symposium", ("NDSS",),
              ("https://www.ndss-symposium.org/",),
              crossref_container_titles=("Network and Distributed System Security Symposium", "NDSS"),
              adapter="ndss"),
    VenueSpec("iclr", "International Conference on Learning Representations", ("ICLR",),
              ("https://openreview.net/group?id=ICLR.cc",),
              ("ICLR.cc/2026/Conference", "ICLR.cc/2025/Conference", "ICLR.cc/2024/Conference"),
              crossref_container_titles=("International Conference on Learning Representations", "ICLR"),
              source_kinds=("openreview",), adapter="openreview"),
    VenueSpec("neurips", "Advances in Neural Information Processing Systems", ("NeurIPS", "NIPS"),
              ("https://neurips.cc/",),
              ("NeurIPS.cc/2026/Conference", "NeurIPS.cc/2025/Conference", "NeurIPS.cc/2024/Conference"),
              crossref_container_titles=("Advances in Neural Information Processing Systems", "NeurIPS"),
              source_kinds=("official", "openreview"), adapter="neurips"),
    VenueSpec("icml", "International Conference on Machine Learning", ("ICML",),
              ("https://icml.cc/",),
              ("ICML.cc/2026/Conference", "ICML.cc/2025/Conference", "ICML.cc/2024/Conference"),
              crossref_container_titles=("Proceedings of the International Conference on Machine Learning", "International Conference on Machine Learning", "ICML"),
              source_kinds=("official", "openreview"), adapter="pmlr"),
    VenueSpec("cvpr", "IEEE/CVF Conference on Computer Vision and Pattern Recognition", ("CVPR",),
              ("https://openaccess.thecvf.com/",),
              crossref_container_titles=("Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition", "IEEE/CVF Conference on Computer Vision and Pattern Recognition", "CVPR"),
              adapter="cvf"),
    VenueSpec("eccv", "European Conference on Computer Vision", ("ECCV",),
              ("https://www.ecva.net/papers.php",),
              crossref_container_titles=("European Conference on Computer Vision", "ECCV"),
              adapter="ecva"),
    VenueSpec("acl", "Annual Meeting of the Association for Computational Linguistics", ("ACL", "ACL Annual Meeting"),
              ("https://www.aclweb.org/portal/",),
              crossref_container_titles=("Proceedings of the Annual Meeting of the Association for Computational Linguistics", "Annual Meeting of the Association for Computational Linguistics", "ACL"),
              adapter="acl_anthology"),
    VenueSpec("emnlp", "Conference on Empirical Methods in Natural Language Processing", ("EMNLP",),
              ("https://2026.emnlp.org/",),
              crossref_container_titles=("Proceedings of the Conference on Empirical Methods in Natural Language Processing", "Conference on Empirical Methods in Natural Language Processing", "EMNLP"),
              adapter="acl_anthology"),
    VenueSpec("aaai", "AAAI Conference on Artificial Intelligence", ("AAAI",),
              ("https://aaai.org/conferences/aaai/",),
              crossref_container_titles=("Proceedings of the AAAI Conference on Artificial Intelligence", "AAAI Conference on Artificial Intelligence", "AAAI"),
              adapter="aaai_ojs"),
    VenueSpec("ijcai", "International Joint Conference on Artificial Intelligence", ("IJCAI",),
              ("https://www.ijcai.org/",),
              crossref_container_titles=("Proceedings of the International Joint Conference on Artificial Intelligence", "International Joint Conference on Artificial Intelligence", "IJCAI"),
              adapter="ijcai"),
    VenueSpec("tdsc", "IEEE Transactions on Dependable and Secure Computing", ("TDSC",),
              ("https://www.computer.org/csdl/journal/tq",),
              crossref_issns=("1545-5971",),
              crossref_container_titles=("IEEE Transactions on Dependable and Secure Computing",),
              source_kinds=("crossref", "ieee_xplore"), adapter="crossref"),
    VenueSpec("tifs", "IEEE Transactions on Information Forensics and Security", ("TIFS",),
              ("https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=10206",),
              crossref_issns=("1556-6013",),
              crossref_container_titles=("IEEE Transactions on Information Forensics and Security",),
              source_kinds=("crossref", "ieee_xplore"), adapter="crossref"),
    VenueSpec("tops", "ACM Transactions on Privacy and Security", ("TOPS", "ACM TOPS"),
              ("https://dl.acm.org/journal/tops",),
              crossref_issns=("2471-2566",),
              crossref_container_titles=("ACM Transactions on Privacy and Security",),
              source_kinds=("crossref",), adapter="crossref"),
)

VENUE_REGISTRY: dict[str, VenueSpec] = {
    alias: spec
    for spec in VENUE_SPECS
    for alias in (spec.key, spec.name, *spec.aliases)
}
_VENUE_ALIASES: dict[str, VenueSpec] = {
    _registry_key(alias): spec
    for spec in VENUE_SPECS
    for alias in (spec.key, spec.name, *spec.aliases, *spec.crossref_container_titles)
}
_OPENREVIEW_VENUES: dict[str, VenueSpec] = {
    venue_id.casefold(): spec for spec in VENUE_SPECS for venue_id in spec.openreview_ids
}


def get_venue_spec(value: str | VenueSpec | None) -> VenueSpec | None:
    if isinstance(value, VenueSpec):
        return value
    if not value or not isinstance(value, str):
        return None
    direct = _VENUE_ALIASES.get(_registry_key(value)) or _OPENREVIEW_VENUES.get(value.strip().casefold())
    if direct is not None:
        return direct
    # OpenReview keeps the same venue family while changing the year.  Defer
    # to each registered spec so an unknown ``*.cc`` family cannot pass.
    for spec in VENUE_SPECS:
        if spec.matches_openreview(value):
            return spec
    return None


@dataclass
class DiscoveryResult:
    """A source response with explicit incomplete records and diagnostics."""

    papers: list[PaperFacts] = field(default_factory=list)
    incomplete: list[dict[str, Any]] = field(default_factory=list)
    reports: list[dict[str, Any]] = field(default_factory=list)


def venue_specs_for_group(groups: list[str] | tuple[str, ...] | None) -> list[VenueSpec]:
    if not groups:
        return list(VENUE_SPECS)
    result: list[VenueSpec] = []
    for group in groups:
        spec = get_venue_spec(group)
        if spec is None:
            raise ValueError(f"unknown venue group: {group}")
        if spec not in result:
            result.append(spec)
    return result


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
    # Extended candidate metadata. These fields are never accepted from
    # Hermes as replacements for facts and remain optional for old snapshots.
    identifiers: dict[str, str] = field(default_factory=dict)
    alternate_links: dict[str, str] = field(default_factory=dict)
    alternate_ids: list[str] = field(default_factory=list)
    venue_evidence: list[dict[str, Any]] = field(default_factory=list)
    source_metadata: dict[str, Any] = field(default_factory=dict)
    collection_tier: str = "unknown"
    match_state: str = "unresolved"
    unresolved_evidence: list[dict[str, Any] | str] = field(default_factory=list)

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
        if self.doi and not re.fullmatch(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", normalize_doi(self.doi), re.IGNORECASE):
            raise ValueError(f"{self.paper_id}: invalid DOI")
        if self.collection_tier not in {"unknown", "formal", "arxiv_fallback"}:
            raise ValueError(f"{self.paper_id}: invalid collection tier")
        if self.match_state not in {"unknown", "canonical", "matched", "unmatched", "unresolved"}:
            raise ValueError(f"{self.paper_id}: invalid match state")

    def validate_materialized(self) -> None:
        self.validate_discovered()
        if not self.bibtex or not self.bibtex_url:
            raise ValueError(f"{self.paper_id}: authoritative BibTeX unavailable")
        if not self.content.get("sha256") or not self.content.get("path"):
            raise ValueError(f"{self.paper_id}: verified full text unavailable")
        if self.unresolved_evidence:
            raise ValueError(f"{self.paper_id}: unresolved evidence remains")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PaperFacts":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        values = {key: item for key, item in value.items() if key in allowed}
        return cls(**values)


DEFAULT_OPENREVIEW_VENUES = [
    "ICLR.cc/2026/Conference",
    "ICLR.cc/2025/Conference",
    "NeurIPS.cc/2026/Conference",
    "NeurIPS.cc/2025/Conference",
    "ICML.cc/2026/Conference",
    "ICML.cc/2025/Conference",
]


@dataclass
class SearchPlan:
    queries: list[str]
    filter_keywords: list[str] = field(default_factory=list)
    date_from: str | None = None
    date_to: str | None = None
    sources: list[str] = field(default_factory=lambda: ["official", "openreview", "crossref", "arxiv"])
    openreview_venues: list[str] = field(default_factory=lambda: list(DEFAULT_OPENREVIEW_VENUES))
    crossref_venues: list[str] = field(default_factory=list)
    # Collection is intentionally bounded for the headless daily job.  A
    # Hermes overlay may tune these values, but SearchPlan validation keeps
    # every source within the same hard upper limits.
    max_results_per_query: int = 50
    max_results_per_venue: int = 50
    scholar_enrich_limit: int = 30
    target: int = 10
    venue_groups: list[str] = field(default_factory=list)
    identifiers: dict[str, list[str] | str] = field(default_factory=dict)

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
        supported = {"arxiv", "official", "openreview", "crossref", "ieee_xplore"}
        if not self.sources:
            raise ValueError("at least one source is required")
        unknown_sources = sorted(set(self.sources) - supported)
        if unknown_sources:
            raise ValueError(f"unsupported sources: {', '.join(unknown_sources)}")
        if len(set(self.sources)) != len(self.sources):
            raise ValueError("sources must not contain duplicates")
        venue_specs_for_group(self.venue_groups)
        for venue in self.openreview_venues:
            spec = get_venue_spec(venue)
            if spec is None or not spec.matches_openreview(venue):
                raise ValueError(f"unknown OpenReview venue: {venue}")
            if "openreview" not in spec.source_kinds:
                raise ValueError(f"OpenReview source is not registered for venue: {venue}")
        for venue in self.crossref_venues:
            spec = get_venue_spec(venue)
            if spec is None or "crossref" not in spec.source_kinds:
                raise ValueError(f"unknown Crossref venue: {venue}")
        if not isinstance(self.identifiers, dict):
            raise ValueError("identifiers must be an object")
        if len(self.identifiers) > 100:
            raise ValueError("identifiers exceeds safety limits")
        bounded_ints = (
            ("max_results_per_query", self.max_results_per_query, 1, 1000),
            ("max_results_per_venue", self.max_results_per_venue, 1, 5000),
            # The daily workflow is deliberately bounded to ten published
            # papers: five core and five broad. Evolution may tune discovery,
            # but it cannot enlarge this materialization budget.
            ("target", self.target, 1, 10),
            ("scholar_enrich_limit", self.scholar_enrich_limit, 0, 100),
        )
        for name, value, lower, upper in bounded_ints:
            # ``bool`` is an ``int`` subclass, and ``int("10")`` would make
            # malformed JSON look valid.  Keep request budgets strictly
            # typed so an overlay cannot smuggle a different schema through
            # coercion.
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{name} must be an integer between {lower} and {upper}")
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
    track: str

    @classmethod
    def load_many(cls, path: Path) -> list["SelectionEntry"]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        values = raw.get("selections") if isinstance(raw, dict) else None
        if not isinstance(values, list):
            raise ValueError("selection must contain a selections array")
        allowed = {"paper_id", "score", "category", "reason", "track"}
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
            track = value.get("track")
            if type(track) is not str or track not in {"core", "broad"}:
                raise ValueError(f"selection {index} must contain track 'core' or 'broad'")
            entry = cls(
                paper_id=str(value.get("paper_id", "")).strip(),
                score=float(value.get("score", 0)),
                category=str(value.get("category", "Other")).strip() or "Other",
                reason=str(value.get("reason", "")).strip(),
                track=track,
            )
            if not entry.paper_id or entry.paper_id in seen:
                raise ValueError(f"selection {index} has a missing or duplicate paper_id")
            seen.add(entry.paper_id)
            entries.append(entry)
        return entries
