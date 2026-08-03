"""Restricted, auditable strategy overlays for the Hermes collector.

Evolution artifacts are deliberately declarative. They can change search and
ranking strategy for a later run, but they cannot change facts, acquisition,
HTTP, materialization, or credential policy.
"""
from __future__ import annotations

import copy
import base64
import hashlib
from html.parser import HTMLParser
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, unquote, urlsplit

from . import config
from .papers.http import HttpClient, HttpResponse
from .papers.models import (
    FACT_FIELDS,
    PaperFacts,
    SearchPlan,
    VENUE_SPECS,
    get_registered_openreview_spec,
    get_registered_venue_spec,
    utc_now,
)


class EvolutionValidationError(ValueError):
    """Raised when an evolution artifact crosses a protected boundary."""


_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b")
_ISO_DATE_RE = re.compile(r"^(?:19|20)\d{2}-\d{2}-\d{2}$")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_SECRET_RE = re.compile(r"(?:api[_-]?key|access[_-]?token|authorization|bearer|password|secret|credential)", re.IGNORECASE)
_SECRET_VALUE_RE = re.compile(
    r"(?:\b(?:api[_-]?key|access[_-]?token)\s*[:=]\s*\S+|\b(?:bearer\s+|sk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
_PROMPT_BOUNDARY_RE = re.compile(
    r"\b(?:ignore|bypass|override|weaken|remove|disable|replace|rewrite|modify|invent|fabricate|guess|fill(?:\s+in)?|set|own|trust)\b"
    r"[^.\n]{0,120}\b(?:fact|facts|title|author|authors|abstract|venue|status|doi|url|bibtex|metadata|provenance)\b",
    re.IGNORECASE,
)
_FACT_ALIAS_TEXT_RE = re.compile(
    r"\b(?:publication[_-]?status|source[_-]?id|paper[_-]?id|paper[_-]?url|landing[_-]?url|pdf[_-]?url|bibtex|authors?|abstract|venue|doi)\b",
    re.IGNORECASE,
)
# Evolution data is strategy-only.  Keep this list broader than the
# ``PaperFacts`` dataclass because Hermes may spell a fact using aliases (for
# example ``paper_url`` or ``source_id``) and can nest it under any overlay
# root.  Catalog/planning keys such as ``source_policy`` remain possible; the
# compact-token check below only rejects complete fact aliases, not arbitrary
# words which happen to contain one of them.
_PROTECTED_KEY_ALIASES = frozenset({
    "title", "titles", "author", "authors", "abstract", "doi", "doiurl",
    "url", "paperurl", "landingurl", "pdf", "pdfurl", "bibtex",
    "venue", "venuename", "publication", "publicationstatus", "status",
    "accepted", "acceptance", "published", "publishedat", "publicationdate",
    "updated", "updatedat", "date", "dates", "year", "years", "source",
    "sourceid", "paperid", "id", "arxivid", "openreviewid", "identifier",
    "identifiers", "alternateid", "alternateids", "alternatelink", "alternatelinks",
    "journalref", "fact", "facts", "provenance", "materialize", "materialized",
    "endpoint", "host", "http", "https", "secret", "token", "credential",
})
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,96}$")

ALLOWED_OVERLAY_ROOTS = frozenset({
    "search_plan", "ranking", "source_policy", "source_requests", "reconciliation", "prompt", "reading_skill",
})
ALLOWED_SEARCH_PLAN_KEYS = frozenset({
    "queries_add", "filter_keywords_add", "core_keywords_add", "venue_groups_add",
    "openreview_venues", "crossref_venues", "max_results_per_query",
    "max_results_per_venue", "scholar_enrich_limit", "target", "date_from", "date_to",
})
ALLOWED_PROMPT_KEYS = frozenset({"fragments_add", "instructions_add", "sections_add"})
ALLOWED_READING_KEYS = frozenset({"fragments_add", "section_ids_add", "queries_add", "max_chars", "max_sections"})
_ALLOWED_TOP_LEVEL = frozenset({
    "version", "proposal_id", "candidate_date", "schema_version", "created_at", "metadata",
    "reflection", "root_cause", "root_cause_md", "overlay", "patch", "tests", "generality",
    "status", "activated_at", "previous_version", "effective_on", "overlay_sha256", "expected_metric", "requires_human_change",
})

_EXPECTED_METRIC_KEYS = frozenset({"name", "direction", "minimum_delta"})
_EXPECTED_METRIC_NAMES = frozenset({
    "query_plan_changes", "positive_cases", "positive_independent_cases",
    "negative_cases", "runner_failures", "fact_mutations",
})
_EXPECTED_METRIC_DIRECTIONS = frozenset({"increase", "decrease", "non_decrease", "non_increase", "unchanged"})

_SOURCE_REQUEST_KEYS = frozenset({"venue_group", "source_key", "path", "parser", "max_bytes"})
_SOURCE_PARSERS = frozenset({"text", "json", "html_links"})
_SOURCE_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/?#-]{0,900}$")
_SOURCE_REQUEST_ROOT_KEYS = frozenset({"requests"})
_SOURCE_MAX_BYTES = 5 * 1024 * 1024
_SOURCE_REPORT_MAX_ITEMS = 100
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# The source key is an adapter identity, not a user-selected host.  Hosts are
# resolved here from the reviewed registry and are never accepted from an
# overlay.  A venue may have more than one official host (for example the
# OpenReview API); the first host is the canonical request target and the
# complete set is retained for response-link validation.
_SOURCE_KEY_HOSTS: dict[str, frozenset[str]] = {
    "openreview": frozenset({"api2.openreview.net", "api.openreview.net"}),
    "arxiv": frozenset({"export.arxiv.org", "arxiv.org"}),
    "crossref": frozenset({"api.crossref.org"}),
    "acl_anthology": frozenset({"aclanthology.org"}),
    "pmlr": frozenset({"proceedings.mlr.press"}),
    "neurips": frozenset({"proceedings.neurips.cc"}),
    "aaai_ojs": frozenset({"ojs.aaai.org"}),
    "ijcai": frozenset({"www.ijcai.org"}),
    "usenix": frozenset({"www.usenix.org"}),
    "ndss": frozenset({"www.ndss-symposium.org"}),
    "ieee": frozenset({"ieeexplore.ieee.org", "www.ieee-security.org"}),
    "ieee_xplore": frozenset({"ieeexploreapi.ieee.org"}),
    "acm": frozenset({"dl.acm.org", "www.sigsac.org"}),
}
_SOURCE_CANONICAL_HOST = {
    "openreview": "api2.openreview.net",
    "arxiv": "export.arxiv.org",
    "crossref": "api.crossref.org",
    "ieee_xplore": "ieeexploreapi.ieee.org",
}


def _source_request_items(value: Any) -> list[dict[str, Any]]:
    """Normalize the two accepted JSON spellings for source requests.

    A list is convenient for a small overlay and ``{"requests": [...]}`` is
    convenient when a future schema needs metadata.  No other keys are
    accepted, and callers always receive a copied list of objects.
    """
    if isinstance(value, list):
        items = value
    elif isinstance(value, dict):
        if set(value) - _SOURCE_REQUEST_ROOT_KEYS:
            raise EvolutionValidationError("source_requests fields are not allowed")
        items = value.get("requests")
    else:
        raise EvolutionValidationError("source_requests must be an array or an object with requests")
    if not isinstance(items, list) or len(items) > _SOURCE_REPORT_MAX_ITEMS:
        raise EvolutionValidationError("source_requests.requests must be a bounded array")
    if not all(isinstance(item, dict) for item in items):
        raise EvolutionValidationError("source_requests entries must be objects")
    return copy.deepcopy(items)


def _validate_source_path(value: Any) -> str:
    if not isinstance(value, str) or not _SOURCE_PATH_RE.fullmatch(value):
        raise EvolutionValidationError("source request path must be a relative URL path beginning with '/'")
    # Validate the effective path as well as its spelling.  HTTP clients and
    # upstream routers may decode percent escapes before applying path
    # normalization, so checking only the raw value lets encoded ``..``,
    # separators, or an encoded authority prefix bypass the namespace checks.
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise EvolutionValidationError("source request path contains an invalid percent escape")
    decoded = value
    for _ in range(32):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    else:
        raise EvolutionValidationError("source request path contains excessive percent encoding")
    # A path is joined to a baseline-owned origin.  Reject all forms that can
    # alter that origin or escape an adapter's path namespace.
    if "\\" in decoded or "\x00" in decoded or decoded.startswith("//"):
        raise EvolutionValidationError("source request path contains an invalid host or separator")
    lowered_path = decoded.casefold().rstrip("/")
    if lowered_path in {
        "/etc", "/var", "/private", "/users", "/home", "/tmp", "/proc", "/dev", "/system",
        "/workspace", "/mnt", "/opt", "/root", "/srv", "/usr", "/bin", "/lib", "/applications",
    } or decoded.casefold().startswith((
        "/etc/", "/var/", "/private/", "/users/", "/home/", "/tmp/", "/proc/", "/dev/", "/system/",
        "/workspace/", "/mnt/", "/opt/", "/root/", "/srv/", "/usr/", "/bin/", "/lib/", "/applications/",
    )):
        raise EvolutionValidationError("source request path is outside the registered web namespace")
    segments = decoded.split("?", 1)[0].split("#", 1)[0].split("/")
    if any(segment in {".", ".."} for segment in segments):
        raise EvolutionValidationError("source request path traversal is not allowed")
    if "://" in decoded or decoded.casefold().startswith(("http:", "https:", "file:")):
        raise EvolutionValidationError("source request path cannot contain a scheme or host")
    return value


def _registered_source_hosts(venue_group: str, source_key: str) -> frozenset[str]:
    """Return hosts allowed for a registered venue/source adapter pair."""
    try:
        spec = get_registered_venue_spec(venue_group)
    except Exception:
        spec = None
    if spec is None:
        raise EvolutionValidationError(f"source request venue_group is not registered: {venue_group}")
    key = normalize_overlay_text(source_key).replace("-", "_")
    aliases = {
        normalize_overlay_text(spec.key).replace("-", "_"),
        normalize_overlay_text(spec.adapter or "").replace("-", "_"),
        *(
            normalize_overlay_text(source_kind).replace("-", "_")
            for source_kind in spec.source_kinds
        ),
    }
    if key not in aliases or not source_key.strip():
        raise EvolutionValidationError(f"source request source_key is not registered for {spec.key}: {source_key}")
    hosts = (
        _SOURCE_KEY_HOSTS.get(source_key.casefold())
        or _SOURCE_KEY_HOSTS.get(key)
        or _SOURCE_KEY_HOSTS.get((spec.adapter or "").casefold())
    )
    if hosts is None:
        # Venue adapters not needing a special API host may use their reviewed
        # official URL host.  This fallback is still registry-derived.
        hosts = frozenset(
            urlsplit(url).hostname.casefold()
            for url in spec.official_urls
            if urlsplit(url).hostname
        )
    if not hosts:
        raise EvolutionValidationError(f"source request has no registered host: {source_key}")
    return hosts


def _validate_collection_path(venue_group: str, source_key: str, path: str) -> None:
    """Reject single-record paths from evolution source requests.

    An overlay may probe a reviewed collection/list endpoint, but a paper ID,
    DOI, or detail-page slug is deliberately resolved by the baseline adapter.
    This keeps experiments general instead of encoding one successful paper.
    """
    spec = get_registered_venue_spec(venue_group)
    adapter = normalize_overlay_text(source_key).replace("-", "_")
    if spec is not None and adapter not in {"crossref", "ieee_xplore", "openreview", "arxiv"}:
        adapter = normalize_overlay_text(spec.adapter or adapter).replace("-", "_")
    decoded_path = path
    for _ in range(32):
        next_value = unquote(decoded_path)
        if next_value == decoded_path:
            break
        decoded_path = next_value
    path_only, _, query = decoded_path.partition("?")
    clean = path_only.rstrip("/") or "/"
    disallowed_patterns = {
        "acl_anthology": (r"^/\d{4}/[^/]+$",),
        "pmlr": (r"^/v\d+/[^/]+\.html$", r"^/v\d+/assets/[^/]+$"),
        "neurips": (r"-abstract-conference(?:\.html)?$", r"-paper-conference(?:\.pdf)?$"),
        "aaai_ojs": (r"/article/view/[^/]+$", r"/issue/view/[^/]+$"),
        "ijcai": (r"^/proceedings/\d{4}/\d+$", r"^/proceedings/\d{4}/bibtex/[^/]+$"),
        "arxiv": (r"^/(?:abs|pdf|bibtex)/[^/]+$",),
        "crossref": (r"^/works/[^/]+$",),
        "ieee_xplore": (r"/(?:document|article)/[^/]+$",),
        "openreview": (r"^/notes/[^/]+$",),
    }.get(adapter, ())
    for pattern in disallowed_patterns:
        if re.search(pattern, clean, re.IGNORECASE):
            raise EvolutionValidationError("source request must target a collection or list path, not one paper")
    query_pairs = parse_qsl(query, keep_blank_values=True)
    for key, value in query_pairs:
        key_norm = normalize_overlay_text(key).replace("-", "_")
        value_text = str(value)
        if key_norm in {"id", "paper_id", "paperid", "forum", "forum_id", "forumid", "doi", "arxiv_id", "arxivid", "article_number", "articlenumber"}:
            raise EvolutionValidationError("source request query cannot select one paper")
        if _DOI_RE.search(value_text) or re.fullmatch(r"(?:\d{4}\.\d{4,5})(?:v\d+)?", value_text.strip()):
            raise EvolutionValidationError("source request query contains a single-paper identifier")


def _validate_source_request(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise EvolutionValidationError("source request must be an object")
    unknown = set(item) - _SOURCE_REQUEST_KEYS
    missing = (_SOURCE_REQUEST_KEYS - {"max_bytes"}) - set(item)
    if unknown or missing:
        raise EvolutionValidationError(
            f"source request fields are invalid (unknown={sorted(unknown)}, missing={sorted(missing)})"
        )
    venue_group = item["venue_group"]
    source_key = item["source_key"]
    if not isinstance(venue_group, str) or not venue_group.strip():
        raise EvolutionValidationError("source request venue_group must be a non-empty string")
    if not isinstance(source_key, str) or not source_key.strip():
        raise EvolutionValidationError("source request source_key must be a non-empty string")
    _registered_source_hosts(venue_group, source_key)
    path = _validate_source_path(item["path"])
    _validate_collection_path(venue_group, source_key, path)
    parser = item["parser"]
    if parser not in _SOURCE_PARSERS:
        raise EvolutionValidationError(f"source request parser is not allowed: {parser}")
    max_bytes = item.get("max_bytes", _SOURCE_MAX_BYTES)
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= _SOURCE_MAX_BYTES:
        raise EvolutionValidationError(f"source request max_bytes must be between 1 and {_SOURCE_MAX_BYTES}")
    return {
        "venue_group": venue_group,
        "source_key": source_key,
        "path": path,
        "parser": parser,
        "max_bytes": max_bytes,
    }


def _validate_source_requests(value: Any) -> list[dict[str, Any]]:
    return [_validate_source_request(item) for item in _source_request_items(value)]


def normalize_overlay_text(value: str) -> str:
    """Return the Unicode casefold key used for overlay de-duplication."""
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def _protected_key(key: Any) -> bool:
    """Recognize fact aliases without rejecting roots like ``source_policy``."""
    compact = "".join(char for char in normalize_overlay_text(str(key)) if char.isalnum())
    return compact in _PROTECTED_KEY_ALIASES


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _baseline_runtime_metadata() -> dict[str, Any]:
    """Return reproducibility identifiers without reading provider secrets."""
    commit = os.getenv("GITHUB_SHA", "").strip()
    if not commit:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=config.PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
                check=False,
            )
            commit = completed.stdout.strip() if completed.returncode == 0 else "unknown"
        except Exception:
            commit = "unknown"
    prompt_path = config.PROJECT_ROOT / "scripts" / "llm_security" / "hermes_prompt.md"
    skill_path = config.PROJECT_ROOT / "src" / "llm_security_digest" / "prompt.py"
    return {
        "baseline_commit": commit or "unknown",
        "overlay_version": None,
        "overlay_sha256": None,
        "prompt_version": _file_sha256(prompt_path),
        "skill_version": _file_sha256(skill_path),
    }


def _contains_forbidden_value(
    value: Any,
    *,
    path: tuple[str, ...] = (),
    reject_years: bool = True,
    reject_dates: bool = True,
) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            key_norm = normalize_overlay_text(key_text).replace("-", "_")
            current = (*path, key_norm)
            if _SECRET_RE.search(key_norm):
                return ".".join(current)
            if _protected_key(key_text):
                return ".".join(current)
            # Registered OpenReview ids necessarily contain a year, and the
            # two explicitly controlled date fields are allowed after their
            # strict schema validation. All other strategy text remains
            # subject to the general hard-coded-year/date guard.
            child_reject_years = reject_years
            child_reject_dates = reject_dates
            if key_norm == "openreview_venues":
                child_reject_years = False
            if key_norm in {"date_from", "date_to"}:
                child_reject_years = False
                child_reject_dates = False
            found = _contains_forbidden_value(
                child,
                path=current,
                reject_years=child_reject_years,
                reject_dates=child_reject_dates,
            )
            if found:
                return found
        return None
    if isinstance(value, list):
        for index, child in enumerate(value):
            found = _contains_forbidden_value(
                child,
                path=(*path, str(index)),
                reject_years=reject_years,
                reject_dates=reject_dates,
            )
            if found:
                return found
        return None
    if isinstance(value, str):
        # DOI/arXiv ids and calendar values are single-record identifiers or
        # facts, never strategy.  ``reject_years`` is retained for callers
        # that need to scan generated metadata separately.
        if (
            _DOI_RE.search(value)
            or re.search(r"\b(?:arxiv|openreview|doi|paper|source)\s*[:/_-]\s*[A-Za-z0-9]", value, re.IGNORECASE)
            or re.search(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b", value)
            or (reject_dates and _DATE_RE.search(value))
            or (reject_years and _YEAR_RE.search(value))
        ):
            return ".".join(path) or "value"
        if value.lstrip().startswith(("http://", "https://")):
            return ".".join(path) or "value"
        if _SECRET_VALUE_RE.search(value):
            return ".".join(path) or "value"
    return None


def _validate_text_array(value: Any, field: str, *, max_items: int = 30, max_length: int = 1000) -> None:
    if not isinstance(value, list) or not value or len(value) > max_items:
        raise EvolutionValidationError(f"{field} must be a non-empty string array")
    if not all(isinstance(item, str) and item.strip() and len(item) <= max_length for item in value):
        raise EvolutionValidationError(f"{field} must contain bounded non-empty strings")


def _validate_iso_date(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        raise EvolutionValidationError(f"{field} must be an ISO date (YYYY-MM-DD)")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise EvolutionValidationError(f"{field} must be an ISO date (YYYY-MM-DD)") from exc
    return value


def _validate_overlay_venues(value: Any, field: str, *, source: str) -> None:
    _validate_text_array(value, field, max_items=30, max_length=500)
    for venue in value:
        if source == "openreview":
            spec = get_registered_openreview_spec(venue)
            if spec is None:
                raise EvolutionValidationError(f"{field} must contain registered OpenReview venue ids: {venue}")
            if "openreview" not in spec.source_kinds:
                raise EvolutionValidationError(f"OpenReview source is not registered for venue: {venue}")
        else:
            spec = get_registered_venue_spec(venue)
            if spec is None:
                raise EvolutionValidationError(f"{field} contains an unregistered venue: {venue}")
            if "crossref" not in spec.source_kinds:
                raise EvolutionValidationError(f"Crossref source is not registered for venue: {venue}")


def _validate_prompt_text_array(value: Any, field: str, *, max_items: int, max_length: int) -> None:
    _validate_text_array(value, field, max_items=max_items, max_length=max_length)
    for item in value:
        if _FACT_ALIAS_TEXT_RE.search(item):
            raise EvolutionValidationError(f"{field} contains a protected fact alias")
        if _PROMPT_BOUNDARY_RE.search(item):
            raise EvolutionValidationError(f"{field} attempts to weaken the baseline fact contract")


def _validate_overlay_shape(overlay: dict[str, Any]) -> None:
    if len(overlay) > 20:
        raise EvolutionValidationError("overlay has too many roots")
    for root, value in overlay.items():
        if root not in ALLOWED_OVERLAY_ROOTS:
            raise EvolutionValidationError(f"overlay root is not allowed: {root}")
        if root == "source_requests":
            # Unlike other roots this contract is naturally represented as a
            # list of request declarations; an object containing ``requests``
            # is accepted for forward-compatible metadata.
            _validate_source_requests(value)
            continue
        if not isinstance(value, dict):
            raise EvolutionValidationError(f"overlay root must be an object: {root}")
        if root == "search_plan":
            unknown = set(value) - ALLOWED_SEARCH_PLAN_KEYS
            if unknown:
                raise EvolutionValidationError(f"search_plan overlay fields are not allowed: {sorted(unknown)}")
            for key in ("queries_add", "filter_keywords_add", "venue_groups_add"):
                if key in value:
                    _validate_text_array(value[key], f"search_plan.{key}", max_items=30, max_length=500)
            if "core_keywords_add" in value:
                _validate_text_array(
                    value["core_keywords_add"],
                    "search_plan.core_keywords_add",
                    max_items=30,
                    max_length=100,
                )
            if "openreview_venues" in value:
                _validate_overlay_venues(value["openreview_venues"], "search_plan.openreview_venues", source="openreview")
            if "crossref_venues" in value:
                _validate_overlay_venues(value["crossref_venues"], "search_plan.crossref_venues", source="crossref")
            for key in ("date_from", "date_to"):
                if key in value:
                    _validate_iso_date(value[key], f"search_plan.{key}")
            if "date_from" in value and "date_to" in value and value["date_from"] > value["date_to"]:
                raise EvolutionValidationError("search_plan.date_from must not be later than date_to")
            for key in ("max_results_per_query", "max_results_per_venue", "scholar_enrich_limit", "target"):
                if key in value and (not isinstance(value[key], int) or isinstance(value[key], bool)):
                    raise EvolutionValidationError(f"search_plan.{key} must be an integer")
        elif root == "prompt":
            unknown = set(value) - ALLOWED_PROMPT_KEYS
            if unknown:
                raise EvolutionValidationError(f"prompt overlay fields are not allowed: {sorted(unknown)}")
            for key in value:
                _validate_prompt_text_array(value[key], f"prompt.{key}", max_items=20, max_length=1200)
        elif root == "reading_skill":
            unknown = set(value) - ALLOWED_READING_KEYS
            if unknown:
                raise EvolutionValidationError(f"reading_skill overlay fields are not allowed: {sorted(unknown)}")
            for key in ("fragments_add", "section_ids_add", "queries_add"):
                if key in value:
                    _validate_prompt_text_array(value[key], f"reading_skill.{key}", max_items=20, max_length=500)
            for key in ("max_chars", "max_sections"):
                if key in value and (not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] <= 0):
                    raise EvolutionValidationError(f"reading_skill.{key} must be a positive integer")
        else:
            if len(value) > 40 or not all(isinstance(key, str) and key.strip() for key in value):
                raise EvolutionValidationError(f"{root} keys must be bounded strings")
            # These roots are declarative policy hints. They may contain only
            # JSON data, never executable source or a source endpoint.
            try:
                encoded = _canonical_json(value)
            except (TypeError, ValueError) as exc:
                raise EvolutionValidationError(f"{root} is not JSON data") from exc
            if len(encoded) > 12000:
                raise EvolutionValidationError(f"{root} overlay is too large")


def _validate_expected_metric(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvolutionValidationError("expected_metric must be an object")
    unknown = set(value) - _EXPECTED_METRIC_KEYS
    missing = _EXPECTED_METRIC_KEYS - set(value)
    if unknown or missing:
        raise EvolutionValidationError(
            f"expected_metric fields are invalid (unknown={sorted(unknown)}, missing={sorted(missing)})"
        )
    name = value.get("name")
    direction = value.get("direction")
    minimum_delta = value.get("minimum_delta")
    if name not in _EXPECTED_METRIC_NAMES:
        raise EvolutionValidationError(f"expected_metric.name is not measurable: {name}")
    if direction not in _EXPECTED_METRIC_DIRECTIONS:
        raise EvolutionValidationError(f"expected_metric.direction is invalid: {direction}")
    if not isinstance(minimum_delta, int) or isinstance(minimum_delta, bool) or not 0 <= minimum_delta <= 100:
        raise EvolutionValidationError("expected_metric.minimum_delta must be an integer between 0 and 100")
    if direction in {"increase", "decrease"} and minimum_delta < 1:
        raise EvolutionValidationError("expected_metric directional changes require minimum_delta >= 1")
    return {"name": name, "direction": direction, "minimum_delta": minimum_delta}


def _validate_reflection(value: Any, *, required: bool = False) -> dict[str, Any]:
    if value is None:
        if required:
            raise EvolutionValidationError(
                "candidate reflection must include root_cause, general_pattern, expected_metric, counterexamples, and regression_tests"
            )
        return {
            "schema_version": 1,
            "summary": "Strategy-only overlay proposal",
            "observed_failure": "Not supplied",
            "evidence": [],
        }
    if not isinstance(value, dict):
        raise EvolutionValidationError("reflection must be an object")
    allowed = {
        "schema_version", "summary", "observed_failure", "evidence", "scope",
        "root_cause", "root_causes", "observations", "affected_invariant",
        "general_pattern", "proposal_type", "expected_metric", "counterexamples",
        "regression_tests", "requires_human_change", "proposal_id", "created_at",
        "overlay_sha256",
    }
    unknown = set(value) - allowed
    if unknown:
        raise EvolutionValidationError(f"reflection fields are not allowed: {sorted(unknown)}")
    if value.get("schema_version", 1) != 1:
        raise EvolutionValidationError("reflection schema_version must be 1")
    for key in ("summary", "observed_failure"):
        if not isinstance(value.get(key), str) or not value[key].strip() or len(value[key]) > 2000:
            raise EvolutionValidationError(f"reflection.{key} must be bounded text")
    for key in ("scope", "root_cause", "affected_invariant", "general_pattern", "proposal_type"):
        if key in value and (not isinstance(value[key], str) or len(value[key]) > 2000):
            raise EvolutionValidationError(f"reflection.{key} must be bounded text")
    required_text = ("root_cause", "general_pattern")
    if required:
        for key in required_text:
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise EvolutionValidationError(f"reflection.{key} is required for an evolution candidate")
        _validate_expected_metric(value.get("expected_metric"))
        for key in ("counterexamples", "regression_tests"):
            if not isinstance(value.get(key), list) or not value[key]:
                raise EvolutionValidationError(f"reflection.{key} must be a non-empty array")
    for key in ("evidence", "observations", "root_causes", "counterexamples", "regression_tests"):
        if key in value and (not isinstance(value[key], list) or len(value[key]) > 50):
            raise EvolutionValidationError(f"reflection.{key} must be a bounded array")
    if "requires_human_change" in value and not isinstance(value["requires_human_change"], bool):
        raise EvolutionValidationError("reflection.requires_human_change must be boolean")
    try:
        if len(_canonical_json(value)) > 24000:
            raise EvolutionValidationError("reflection is too large")
    except (TypeError, ValueError) as exc:
        raise EvolutionValidationError("reflection must contain JSON data") from exc
    scan_value = {key: item for key, item in value.items() if key not in {"proposal_id", "created_at", "overlay_sha256"}}
    if _contains_forbidden_value(scan_value) is not None:
        raise EvolutionValidationError("reflection contains paper-specific or protected data")
    return copy.deepcopy(value)


def _validate_candidate_tests(value: Any, *, required: bool = False) -> list[dict[str, Any]]:
    if value is None:
        if required:
            raise EvolutionValidationError("candidate tests must include a trigger, two positive, and one negative fixture")
        return []
    if not isinstance(value, list) or len(value) > 50:
        raise EvolutionValidationError("candidate tests must be a bounded array")
    result: list[dict[str, Any]] = []
    for index, case in enumerate(value):
        if not isinstance(case, dict):
            raise EvolutionValidationError(f"candidate test {index} must be an object")
        allowed = {"name", "kind", "plan", "expected", "independent", "invariant_proof", "dimensions"}
        unknown = set(case) - allowed
        if unknown:
            raise EvolutionValidationError(f"candidate test fields are not allowed: {sorted(unknown)}")
        name = case.get("name", f"case-{index + 1}")
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise EvolutionValidationError(f"candidate test {index} has an invalid name")
        kind = case.get("kind", "positive")
        if kind not in {"trigger", "positive", "negative"}:
            raise EvolutionValidationError(f"candidate test {index} kind is invalid")
        if "independent" in case and not isinstance(case["independent"], bool):
            raise EvolutionValidationError(f"candidate test {index}.independent must be boolean")
        if "invariant_proof" in case and (
            not isinstance(case["invariant_proof"], str) or not case["invariant_proof"].strip() or len(case["invariant_proof"]) > 2000
        ):
            raise EvolutionValidationError(f"candidate test {index}.invariant_proof is invalid")
        if "dimensions" in case:
            dimensions = case["dimensions"]
            if not isinstance(dimensions, dict) or not dimensions or set(dimensions) - {"source", "venue", "year"}:
                raise EvolutionValidationError(f"candidate test {index}.dimensions is invalid")
            if not all(isinstance(item, str) and item.strip() and len(item) <= 80 for item in dimensions.values()):
                raise EvolutionValidationError(f"candidate test {index}.dimensions values are invalid")
        plan = case.get("plan", {})
        queries = plan.get("queries", ["security"]) if isinstance(plan, dict) else None
        if not isinstance(plan, dict) or not isinstance(queries, list) or not queries or len(queries) > 30:
            raise EvolutionValidationError(f"candidate test {index} plan is invalid")
        if not all(isinstance(query, str) and query.strip() and len(query) <= 500 for query in queries):
            raise EvolutionValidationError(f"candidate test {index} plan queries are invalid")
        scan_case = {key: item for key, item in case.items() if key != "dimensions"}
        if _contains_forbidden_value(scan_case) is not None:
            raise EvolutionValidationError(f"candidate test {index} contains protected or hard-coded data")
        normalized = copy.deepcopy(case)
        normalized.setdefault("independent", True)
        result.append(normalized)
    if required:
        trigger_count = sum(item.get("kind") == "trigger" for item in result)
        positive_count = sum(item.get("kind") == "positive" for item in result)
        negative_count = sum(item.get("kind") == "negative" for item in result)
        if trigger_count < 1 or positive_count < 2 or negative_count < 1:
            raise EvolutionValidationError(
                "candidate tests require at least one trigger, two positive, and one negative fixture"
            )
        positive_plans = {_sha256(item.get("plan", {})) for item in result if item.get("kind") == "positive" and item.get("independent") is not False}
        if len(positive_plans) < 2:
            raise EvolutionValidationError("candidate positive fixtures must use two independent plans")
    return result


def validate_overlay(
    candidate: dict[str, Any], *, require_evidence: bool = False, allow_legacy_source_overlay: bool = False
) -> dict[str, Any]:
    """Validate and return a deep-copied strategy candidate."""
    if not isinstance(candidate, dict):
        raise EvolutionValidationError("evolution candidate must be an object")
    top_level_facts = set(candidate) & FACT_FIELDS
    if top_level_facts:
        raise EvolutionValidationError(f"candidate contains fact fields: {sorted(top_level_facts)}")
    unknown_top_level = set(candidate) - _ALLOWED_TOP_LEVEL
    if unknown_top_level:
        raise EvolutionValidationError(f"candidate fields are not allowed: {sorted(unknown_top_level)}")
    version = str(candidate.get("version", "")).strip()
    proposal_id = str(candidate.get("proposal_id", version)).strip()
    if version and not _VERSION_RE.fullmatch(version):
        raise EvolutionValidationError("candidate version is invalid")
    if proposal_id and not _VERSION_RE.fullmatch(proposal_id):
        raise EvolutionValidationError("candidate proposal_id is invalid")
    overlay = candidate.get("overlay", candidate.get("patch"))
    if not isinstance(overlay, dict):
        raise EvolutionValidationError("candidate must contain an overlay object")
    # ``sources`` was accepted by schema v2 before it became a protected
    # baseline policy. Existing immutable manifests must remain auditable, but
    # their old source list must never affect a future collection run.
    validation_overlay = overlay
    search_plan = overlay.get("search_plan")
    if isinstance(search_plan, dict) and "sources" in search_plan:
        if not allow_legacy_source_overlay:
            _validate_overlay_shape(overlay)
        _validate_text_array(search_plan["sources"], "search_plan.sources", max_items=30, max_length=100)
        validation_overlay = copy.deepcopy(overlay)
        validation_overlay["search_plan"].pop("sources")
    _validate_overlay_shape(validation_overlay)
    # Registered request paths may legitimately contain a year (for example a
    # proceedings volume).  They are still checked for DOI/URL/secret values,
    # while the generic strategy roots retain the anti-hard-coding year gate.
    forbidden = None
    for root, value in overlay.items():
        found = _contains_forbidden_value(value, reject_years=root != "source_requests")
        if found:
            forbidden = f"{root}.{found}"
            break
    if forbidden:
        raise EvolutionValidationError(f"overlay contains protected or hard-coded value at {forbidden}")
    metadata = candidate.get("metadata", {})
    if metadata is not None and not isinstance(metadata, dict):
        raise EvolutionValidationError("candidate metadata must be an object")
    if isinstance(metadata, dict):
        forbidden_metadata = set(metadata) & FACT_FIELDS
        if forbidden_metadata:
            raise EvolutionValidationError(f"candidate metadata contains fact fields: {sorted(forbidden_metadata)}")
        for key, value in metadata.items():
            if _SECRET_RE.search(str(key)) or (isinstance(value, str) and value.lstrip().startswith(("http://", "https://"))):
                raise EvolutionValidationError(f"candidate metadata contains protected field: {key}")
        if _contains_forbidden_value(metadata) is not None:
            raise EvolutionValidationError("candidate metadata contains hard-coded value")
    reflection = _validate_reflection(candidate.get("reflection"), required=require_evidence)
    root_cause = candidate.get("root_cause_md", candidate.get("root_cause"))
    if root_cause is None and require_evidence:
        root_cause = reflection.get("root_cause")
    if root_cause is None:
        root_cause = "Strategy-only overlay proposal."
    if not isinstance(root_cause, str) or not root_cause.strip() or len(root_cause) > 10000:
        raise EvolutionValidationError("root cause must be bounded text")
    if _contains_forbidden_value(root_cause) is not None:
        raise EvolutionValidationError("root cause contains paper-specific or protected data")
    tests = _validate_candidate_tests(candidate.get("tests"), required=require_evidence)
    if require_evidence:
        reflection_tests = reflection.get("regression_tests", [])
        reflection_names = {
            item.get("name") if isinstance(item, dict) else item
            for item in reflection_tests
        }
        test_names = {item.get("name") for item in tests}
        if not reflection_names or not reflection_names.issubset(test_names):
            raise EvolutionValidationError("reflection.regression_tests must name candidate fixtures")
    generality = candidate.get("generality", {})
    if generality is None:
        generality = {}
    if not isinstance(generality, dict):
        raise EvolutionValidationError("generality must be an object")
    if set(generality) - {"invariant_proof"}:
        raise EvolutionValidationError("generality fields are not allowed")
    if "invariant_proof" in generality:
        proof = generality["invariant_proof"]
        if not isinstance(proof, str) or not proof.strip() or len(proof) > 2000:
            raise EvolutionValidationError("generality.invariant_proof is invalid")
        if _contains_forbidden_value(proof, path=("generality", "invariant_proof")) is not None:
            raise EvolutionValidationError("generality.invariant_proof contains protected or hard-coded data")
    result = copy.deepcopy(candidate)
    result["overlay"] = copy.deepcopy(overlay)
    result.pop("patch", None)
    result["reflection"] = reflection
    result["root_cause_md"] = root_cause
    result["tests"] = tests
    result["generality"] = copy.deepcopy(generality)
    if require_evidence:
        expected_metric = candidate.get("expected_metric", reflection.get("expected_metric"))
        result["expected_metric"] = _validate_expected_metric(expected_metric)
        if reflection.get("expected_metric") != result["expected_metric"]:
            raise EvolutionValidationError("candidate expected_metric must match reflection.expected_metric")
        top_human_change = candidate.get("requires_human_change", False)
        if not isinstance(top_human_change, bool):
            raise EvolutionValidationError("candidate.requires_human_change must be boolean")
        if reflection.get("requires_human_change") is True or top_human_change:
            result["requires_human_change"] = True
    if version:
        result["version"] = version
    if proposal_id:
        result["proposal_id"] = proposal_id
    return result


def prepare_candidate(candidate: dict[str, Any], *, allow_legacy_source_overlay: bool = False) -> dict[str, Any]:
    result = validate_overlay(
        candidate,
        require_evidence=True,
        allow_legacy_source_overlay=allow_legacy_source_overlay,
    )
    if not result.get("version"):
        digest = _sha256(result["overlay"])[:16]
        result["version"] = f"candidate-{digest}"
    result.setdefault("proposal_id", result["version"])
    result.setdefault("schema_version", 2)
    result.setdefault("created_at", utc_now())
    if not isinstance(result["created_at"], str) or len(result["created_at"]) < 10:
        raise EvolutionValidationError("created_at must be an ISO timestamp")
    result.setdefault("candidate_date", result["created_at"][:10])
    try:
        datetime.strptime(str(result["candidate_date"]), "%Y-%m-%d")
    except ValueError as exc:
        raise EvolutionValidationError("candidate_date must be YYYY-MM-DD") from exc
    result["overlay_sha256"] = _sha256(result["overlay"])
    return result


def apply_overlay(plan: SearchPlan | dict[str, Any], overlay: dict[str, Any] | None) -> SearchPlan:
    """Apply a validated strategy overlay to a plan for the next run."""
    raw = asdict(plan) if isinstance(plan, SearchPlan) else copy.deepcopy(plan)
    # Legacy active manifests can retain their original source-list data for
    # integrity/replay, but this function never applies that deprecated field.
    validated = validate_overlay(
        {"overlay": overlay or {}}, allow_legacy_source_overlay=True
    )["overlay"]
    search = validated.get("search_plan", {})
    for key, target in (
        ("queries_add", "queries"),
        ("filter_keywords_add", "filter_keywords"),
        ("core_keywords_add", "core_keywords"),
        ("venue_groups_add", "venue_groups"),
    ):
        values = search.get(key)
        if values:
            existing = list(raw.get(target) or [])
            seen = {normalize_overlay_text(item) for item in existing}
            for value in values:
                if normalize_overlay_text(value) not in seen:
                    # Core terms are later matched against authoritative text;
                    # keep their displayed spelling but never retain proposal
                    # whitespace as part of the search term.
                    existing.append(value.strip() if target == "core_keywords" else value)
                    seen.add(normalize_overlay_text(value))
            raw[target] = existing
    for key in (
        "openreview_venues", "crossref_venues", "date_from", "date_to",
        "max_results_per_query", "max_results_per_venue", "scholar_enrich_limit", "target",
    ):
        if key in search:
            raw[key] = copy.deepcopy(search[key])
    result = SearchPlan(**{field: raw[field] for field in SearchPlan.__dataclass_fields__ if field in raw})
    result.validate()
    return result


RUNNER_SCHEMA_VERSION = 1
RUNNER_MAX_STDOUT = 64 * 1024
RUNNER_MAX_STDERR = 8 * 1024
RUNNER_TIMEOUT_SECONDS = 10
RUNNER_MAX_STDIN = 8 * 1024 * 1024
_RUNNER_OUTPUT_KEYS = frozenset({
    "schema_version", "strategy", "raw_candidates", "evidence",
    "prompt_fragments", "reading_skill_fragments", "source_reports",
})
_RUNNER_FACT_KEYS = frozenset(FACT_FIELDS) | {
    "paper_id", "source", "source_id", "identifiers", "alternate_ids",
    "alternate_links", "provenance", "facts", "facts.json",
}


class EvolutionRunnerError(EvolutionValidationError):
    """Raised when an overlay subprocess crosses its runner contract."""


def _contains_runner_fact_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            compact = "".join(char for char in normalize_overlay_text(str(key)) if char.isalnum() or char == ".")
            if compact in {"".join(char for char in item.casefold() if char.isalnum() or char == ".") for item in _RUNNER_FACT_KEYS}:
                return str(key)
            found = _contains_runner_fact_key(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _contains_runner_fact_key(child)
            if found:
                return found
    return None


_SOURCE_REPORT_KEYS = frozenset({
    "venue_group", "source_key", "path", "parser", "stage", "status", "http_status",
    "requests_attempted", "requests_succeeded", "requests_failed",
    "response_sha256", "response_bytes", "records_scanned", "records_valid",
    "records_filtered", "records_incomplete", "links", "json_type", "json_keys_count",
    "line_count", "error_type", "error",
})


def _validate_source_reports(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > _SOURCE_REPORT_MAX_ITEMS:
        raise EvolutionRunnerError("overlay runner source_reports must be a bounded array")
    result: list[dict[str, Any]] = []
    for index, report in enumerate(value):
        if not isinstance(report, dict):
            raise EvolutionRunnerError(f"source report {index} must be an object")
        unknown = set(report) - _SOURCE_REPORT_KEYS
        if unknown:
            raise EvolutionRunnerError(f"source report fields are not allowed: {sorted(unknown)}")
        if _contains_runner_fact_key(report):
            raise EvolutionRunnerError("source report contains a protected fact field")
        if report.get("parser") not in _SOURCE_PARSERS:
            raise EvolutionRunnerError("source report parser is invalid")
        if report.get("stage") not in {"request", "parse"}:
            raise EvolutionRunnerError("source report stage is invalid")
        status = report.get("status")
        if status not in {"ok", "error"}:
            raise EvolutionRunnerError("source report status is invalid")
        if not isinstance(report.get("venue_group"), str) or not isinstance(report.get("source_key"), str):
            raise EvolutionRunnerError("source report source identity is invalid")
        if not isinstance(report.get("path"), str) or not report["path"].startswith("/"):
            raise EvolutionRunnerError("source report path is invalid")
        links = report.get("links", [])
        if not isinstance(links, list) or len(links) > 200 or not all(isinstance(link, str) for link in links):
            raise EvolutionRunnerError("source report links are invalid")
        for key in (
            "response_bytes", "requests_attempted", "requests_succeeded", "requests_failed",
            "records_scanned", "records_valid", "records_filtered", "records_incomplete",
            "line_count", "json_keys_count",
        ):
            if key in report and (not isinstance(report[key], int) or isinstance(report[key], bool) or report[key] < 0):
                raise EvolutionRunnerError(f"source report {key} is invalid")
        if "response_sha256" in report and (
            not isinstance(report["response_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", report["response_sha256"])
        ):
            raise EvolutionRunnerError("source report response hash is invalid")
        result.append(copy.deepcopy(report))
    return result


class _LinkParser(HTMLParser):
    """Small deterministic parser used by the enum-only worker."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        for key, value in attrs:
            if key.casefold() != "href" or not value:
                continue
            href = value.strip()
            parsed = urlsplit(href)
            if parsed.scheme or parsed.netloc:
                # An overlay receives links only as same-origin path evidence;
                # absolute links cannot smuggle a new host into later stages.
                continue
            if not href.startswith("/") or href.startswith("//"):
                continue
            try:
                clean = _validate_source_path(href)
            except EvolutionValidationError:
                continue
            if clean not in self.links:
                self.links.append(clean)
            if len(self.links) >= 200:
                return


def _parse_source_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """Parse a broker response using one of the reviewed parser enums."""
    request = fixture.get("request")
    if not isinstance(request, dict):
        raise EvolutionRunnerError("source fixture request is invalid")
    parser = request.get("parser")
    if parser not in _SOURCE_PARSERS:
        raise EvolutionRunnerError("source fixture parser is invalid")
    encoded = fixture.get("body_base64")
    if not isinstance(encoded, str) or len(encoded) > RUNNER_MAX_STDIN:
        raise EvolutionRunnerError("source fixture body is invalid")
    try:
        body = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise EvolutionRunnerError("source fixture body is not valid base64") from exc
    if len(body) > int(request.get("max_bytes", _SOURCE_MAX_BYTES)):
        raise EvolutionRunnerError("source fixture exceeds declared response limit")
    report: dict[str, Any] = {
        "venue_group": request["venue_group"],
        "source_key": request["source_key"],
        "path": request["path"],
        "parser": parser,
        "stage": "parse",
        "status": "ok" if int(fixture.get("status", 200)) < 400 else "error",
        "http_status": int(fixture.get("status", 200)),
        "requests_attempted": 1,
        "requests_succeeded": 1 if int(fixture.get("status", 200)) < 400 else 0,
        "requests_failed": 0 if int(fixture.get("status", 200)) < 400 else 1,
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "response_bytes": len(body),
    }
    if report["status"] == "error":
        report["error_type"] = "http_status"
        report["error"] = f"HTTP {report['http_status']}"
    try:
        if parser == "text":
            text_value = body.decode("utf-8", errors="replace")
            report["line_count"] = text_value.count("\n") + (1 if text_value else 0)
        elif parser == "json":
            parsed = json.loads(body.decode("utf-8"))
            if isinstance(parsed, dict):
                report["json_type"] = "object"
                # Only cardinality is returned.  Values and field names are
                # source facts and remain in the baseline adapter process.
                report["json_keys_count"] = len(parsed)
                report["records_scanned"] = len(parsed.get("items", [])) if isinstance(parsed.get("items"), list) else 0
            elif isinstance(parsed, list):
                report["json_type"] = "array"
                report["records_scanned"] = len(parsed)
            else:
                report["json_type"] = type(parsed).__name__
                report["records_scanned"] = 0
        else:
            parser_instance = _LinkParser()
            parser_instance.feed(body.decode("utf-8", errors="replace"))
            parser_instance.close()
            report["links"] = parser_instance.links
            report["records_scanned"] = len(parser_instance.links)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        report["status"] = "error"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:300]
    return report


def _runner_output(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvolutionRunnerError("overlay runner output must be an object")
    unknown = set(value) - _RUNNER_OUTPUT_KEYS
    if unknown:
        raise EvolutionRunnerError(f"overlay runner output fields are not allowed: {sorted(unknown)}")
    if value.get("schema_version") != RUNNER_SCHEMA_VERSION:
        raise EvolutionRunnerError("overlay runner schema version is invalid")
    strategy = value.get("strategy")
    if not isinstance(strategy, dict) or set(strategy) != {"plan"} or not isinstance(strategy["plan"], dict):
        raise EvolutionRunnerError("overlay runner strategy must contain only a plan object")
    try:
        plan = SearchPlan(**strategy["plan"])
        plan.validate()
    except Exception as exc:
        raise EvolutionRunnerError("overlay runner returned an invalid SearchPlan") from exc
    for key in ("raw_candidates", "evidence"):
        items = value.get(key, [])
        if not isinstance(items, list) or len(items) > 100:
            raise EvolutionRunnerError(f"overlay runner {key} must be a bounded array")
        if not all(isinstance(item, dict) for item in items):
            raise EvolutionRunnerError(f"overlay runner {key} entries must be objects")
        forbidden = _contains_runner_fact_key(items)
        if forbidden:
            raise EvolutionRunnerError(f"overlay runner output contains protected fact field: {forbidden}")
    for key in ("prompt_fragments", "reading_skill_fragments"):
        fragments = value.get(key, {})
        if not isinstance(fragments, dict):
            raise EvolutionRunnerError(f"overlay runner {key} must be an object")
        forbidden = _contains_runner_fact_key(fragments)
        if forbidden:
            raise EvolutionRunnerError(f"overlay runner output contains protected fact field: {forbidden}")
    source_reports = _validate_source_reports(value.get("source_reports", []))
    value = {**value, "source_reports": source_reports}
    return copy.deepcopy(value)


class BaselineHttpBroker:
    """Baseline-owned HTTPS broker for experimental source adapters.

    The broker is intentionally small: adapters can only request registered
    hosts and receive the normal authoritative ``HttpResponse``.  It never
    exposes the process environment or credentials to an overlay.
    """

    _STATIC_HOSTS = frozenset({
        "api2.openreview.net", "api.openreview.net", "openreview.net",
        "arxiv.org", "export.arxiv.org", "api.crossref.org",
        "proceedings.mlr.press", "proceedings.neurips.cc", "aclanthology.org",
        "ojs.aaai.org", "www.ijcai.org", "www.usenix.org",
        "www.ndss-symposium.org", "ieeexplore.ieee.org", "ieeexploreapi.ieee.org", "www.ieee-security.org",
        "dl.acm.org", "www.sigsac.org", "icml.cc", "neurips.cc",
    })

    _SAFE_RESPONSE_HEADERS = frozenset({
        "cache-control", "content-encoding", "content-length", "content-type",
        "etag", "last-modified", "retry-after", "vary",
    })
    _SENSITIVE_HEADER_RE = re.compile(r"(?:authorization|cookie|set-cookie|proxy-auth|api[_-]?key|token|secret)", re.IGNORECASE)

    def __init__(self, client: HttpClient | None = None, *, allowed_hosts: Iterable[str] | None = None):
        registered = {
            urlsplit(url).hostname.casefold()
            for spec in VENUE_SPECS
            for url in spec.official_urls
            if urlsplit(url).hostname
        }
        self.allowed_hosts = frozenset({*self._STATIC_HOSTS, *registered, *(str(item).casefold() for item in (allowed_hosts or ()))})
        self.client = client or HttpClient(user_agent="LLMSecurityDigest-EvolutionBroker/1.0")

    @staticmethod
    def _sanitize_headers(headers: Any) -> dict[str, str]:
        if not isinstance(headers, dict):
            return {}
        return {
            str(key).lower(): str(value)
            for key, value in headers.items()
            if not BaselineHttpBroker._SENSITIVE_HEADER_RE.search(str(key))
            and str(key).lower() in BaselineHttpBroker._SAFE_RESPONSE_HEADERS
        }

    def _source_url(self, request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        normalized = _validate_source_request(request)
        hosts = _registered_source_hosts(normalized["venue_group"], normalized["source_key"])
        # Sorting makes host selection stable when a source has v1/v2 aliases.
        key = normalize_overlay_text(normalized["source_key"]).replace("-", "_")
        spec = get_registered_venue_spec(normalized["venue_group"])
        adapter_key = normalize_overlay_text(spec.adapter if spec else "").replace("-", "_")
        host = _SOURCE_CANONICAL_HOST.get(key) or _SOURCE_CANONICAL_HOST.get(adapter_key) or next(iter(sorted(hosts)))
        return f"https://{host}{normalized['path']}", normalized

    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        try:
            parsed = urlsplit(str(url))
            host = (parsed.hostname or "").casefold()
            port = parsed.port
        except ValueError as exc:
            raise EvolutionRunnerError("HTTP broker rejected malformed URL") from exc
        if parsed.scheme.casefold() != "https" or not host or host not in self.allowed_hosts or parsed.username or parsed.password or port is not None:
            raise EvolutionRunnerError(f"HTTP broker rejected host: {host or '<missing>'}")
        # An overlay has no header interface.  Even baseline callers cannot
        # pass credentials through this broker, and response headers are
        # reduced to non-sensitive cache/content metadata below.
        kwargs = dict(kwargs)
        kwargs.pop("headers", None)
        max_bytes = kwargs.get("max_bytes", _SOURCE_MAX_BYTES)
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= _SOURCE_MAX_BYTES:
            raise EvolutionRunnerError("HTTP broker max_bytes is outside the safety bound")
        kwargs["max_bytes"] = max_bytes
        response = self.client.get(url, **kwargs)
        if not isinstance(response, HttpResponse):
            raise EvolutionRunnerError("HTTP broker client returned an invalid response")
        if len(response.body) > max_bytes:
            raise EvolutionRunnerError("HTTP broker response exceeds the safety bound")
        return HttpResponse(
            url=response.url,
            status=response.status,
            headers=self._sanitize_headers(response.headers),
            body=response.body,
        )

    def request(self, request: dict[str, Any]) -> tuple[dict[str, Any], HttpResponse | None]:
        """Fetch one declarative request and return a redacted source report.

        The report is returned even when the upstream is unavailable so the
        source failure remains observable; no fallback data is synthesized.
        """
        normalized = _validate_source_request(request)
        report = {
            "venue_group": normalized["venue_group"],
            "source_key": normalized["source_key"],
            "path": normalized["path"],
            "parser": normalized["parser"],
            "stage": "request",
            "requests_attempted": 1,
            "requests_succeeded": 0,
            "requests_failed": 1,
        }
        try:
            url, normalized = self._source_url(normalized)
            response = self.get(url, max_bytes=normalized["max_bytes"])
            report.update({
                "status": "ok" if response.status < 400 else "error",
                "http_status": response.status,
                "requests_succeeded": 1 if response.status < 400 else 0,
                "requests_failed": 0 if response.status < 400 else 1,
            })
            return report, response
        except Exception as exc:
            report.update({"status": "error", "error_type": type(exc).__name__, "error": str(exc)[:300]})
            return report, None


class EvolutionRunner:
    """Run declarative strategy overlays in an isolated JSON subprocess."""

    def __init__(
        self,
        *,
        timeout: float = RUNNER_TIMEOUT_SECONDS,
        max_stdout: int = RUNNER_MAX_STDOUT,
        max_stdin: int = RUNNER_MAX_STDIN,
        broker: BaselineHttpBroker | None = None,
    ):
        self.timeout = float(timeout)
        self.max_stdout = int(max_stdout)
        self.max_stdin = int(max_stdin)
        self.broker = broker or BaselineHttpBroker()

    def run(self, plan: SearchPlan, overlay: dict[str, Any] | None) -> dict[str, Any]:
        validated = validate_overlay({"overlay": overlay or {}})["overlay"]
        source_reports: list[dict[str, Any]] = []
        source_fixtures: list[dict[str, Any]] = []
        for request in _validate_source_requests(validated.get("source_requests", [])):
            report, response = self.broker.request(request)
            if response is None:
                source_reports.append(report)
                continue
            source_fixtures.append({
                "request": request,
                "status": response.status,
                "headers": response.headers,
                "body_base64": base64.b64encode(response.body).decode("ascii"),
            })
        payload = _canonical_json({
            "schema_version": RUNNER_SCHEMA_VERSION,
            "plan": asdict(plan),
            "overlay": validated,
            "source_fixtures": source_fixtures,
            "source_reports": source_reports,
        }).encode("utf-8")
        if len(payload) > self.max_stdin:
            raise EvolutionRunnerError("overlay runner stdin exceeds size limit")
        with tempfile.TemporaryDirectory(prefix="llmsd-evolution-") as temp_dir:
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": str(config.PROJECT_ROOT / "src"),
                "TMPDIR": temp_dir,
                "LANG": "C.UTF-8",
            }
            try:
                completed = subprocess.run(
                    [sys.executable, "-m", "llm_security_digest.evolution", "--overlay-worker"],
                    input=payload,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=temp_dir,
                    env=env,
                    timeout=self.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise EvolutionRunnerError("overlay runner timed out") from exc
        if len(completed.stdout) > self.max_stdout:
            raise EvolutionRunnerError("overlay runner stdout exceeds size limit")
        if len(completed.stderr) > RUNNER_MAX_STDERR:
            raise EvolutionRunnerError("overlay runner stderr exceeds size limit")
        if completed.returncode != 0:
            detail = completed.stderr[:RUNNER_MAX_STDERR].decode("utf-8", errors="replace")
            raise EvolutionRunnerError(f"overlay runner failed ({completed.returncode}): {detail[:300]}")
        try:
            output = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvolutionRunnerError("overlay runner returned invalid JSON") from exc
        return _runner_output(output)


def run_overlay_subprocess(plan: SearchPlan, overlay: dict[str, Any] | None, *, timeout: float = RUNNER_TIMEOUT_SECONDS, max_stdout: int = RUNNER_MAX_STDOUT) -> dict[str, Any]:
    """Public bounded runner used by validation and shadow execution."""
    return EvolutionRunner(timeout=timeout, max_stdout=max_stdout).run(plan, overlay)


def prompt_fragments(context: dict[str, Any] | None = None, overlay: dict[str, Any] | None = None) -> dict[str, list[str]]:
    """Return additive prompt fragments after the baseline contract is checked.

    The baseline prompt is owned by :mod:`llm_security_digest.prompt`; this
    helper only exposes bounded additions and never accepts a replacement
    contract or a fact-bearing field.
    """
    candidate = overlay if overlay is not None else (context or {})
    if isinstance(candidate, dict) and "overlay" in candidate:
        candidate = candidate.get("overlay")
    value = validate_overlay({"overlay": candidate or {}})["overlay"].get("prompt", {})
    return {
        key: list(values)
        for key, values in value.items()
        if key in ALLOWED_PROMPT_KEYS and isinstance(values, list)
    }


def reading_skill_fragments(context: dict[str, Any] | None = None, overlay: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return additive bounded reading-skill fragments; baseline skill wins."""
    candidate = overlay if overlay is not None else (context or {})
    if isinstance(candidate, dict) and "overlay" in candidate:
        candidate = candidate.get("overlay")
    value = validate_overlay({"overlay": candidate or {}})["overlay"].get("reading_skill", {})
    return copy.deepcopy(value)


def _safe_write_json(path: Path, value: Any, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if exclusive and path.exists():
        raise FileExistsError(path)
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _safe_write_text(path: Path, value: str, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if exclusive and path.exists():
        raise FileExistsError(path)
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _safe_rejection_reason(value: Any) -> str:
    """Bound rejection details without persisting URLs or credential values."""
    try:
        text = str(value).strip()
    except Exception:
        return "candidate rejected"
    if not text:
        return "candidate rejected"
    text = re.sub(r"(?i)\bhttps?://[^\s<>'\"]+", "[redacted-url]", text)
    text = re.sub(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|authorization|bearer|password|secret|credential)\b"
        r"\s*(?:[:=]\s*|\s+)\S+",
        "[redacted]",
        text,
    )
    return text[:500]


@dataclass(frozen=True)
class EvolutionPaths:
    root: Path

    @property
    def candidates(self) -> Path:
        return self.root / "candidates"

    @property
    def shadow(self) -> Path:
        return self.root / "shadow"

    @property
    def active(self) -> Path:
        return self.root / "active"

    @property
    def rejected(self) -> Path:
        return self.root / "rejected"

    @property
    def history(self) -> Path:
        return self.root / "history"

    @property
    def active_json(self) -> Path:
        # The pointer is deliberately outside immutable version directories,
        # matching the documented ``evolution/active.json`` contract.
        return self.root / "active.json"

    @property
    def legacy_active_json(self) -> Path:
        return self.active / "active.json"


def _candidate_source_name(candidate: dict[str, Any]) -> str:
    return str(candidate.get("proposal_id") or candidate.get("version") or "proposal")


class EvolutionStore:
    def __init__(self, root: Path | None = None):
        self.paths = EvolutionPaths((root or config.EVOLUTION_ROOT).resolve())
        for path in (self.paths.candidates, self.paths.shadow, self.paths.active, self.paths.rejected, self.paths.history):
            path.mkdir(parents=True, exist_ok=True)

    def _candidate_dir(self, candidate: dict[str, Any]) -> Path:
        return self.paths.candidates / str(candidate["candidate_date"]) / _candidate_source_name(candidate)

    def _shadow_report_path(self, candidate: dict[str, Any]) -> Path:
        return self.paths.shadow / str(candidate["candidate_date"]) / _candidate_source_name(candidate) / "report.json"

    @staticmethod
    def _validate_component(value: str | Path, *, label: str) -> str:
        text = str(value)
        if not _VERSION_RE.fullmatch(text):
            raise EvolutionValidationError(f"{label} contains an unsafe path component")
        return text

    def _assert_inside_root(self, path: Path) -> Path:
        root = self.paths.root.resolve()
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise EvolutionValidationError("evolution path escapes the data directory") from exc
        return resolved

    def _resolve_candidate_path(self, version_or_path: str | Path) -> Path:
        raw = str(version_or_path)
        # Reject traversal before checking existence.  Otherwise a caller can
        # use an existing path outside the evolution root as a candidate.
        raw_parts = Path(raw).parts
        if ".." in raw_parts:
            raise EvolutionValidationError("candidate path traversal is not allowed")
        path = Path(raw)
        if path.exists():
            path = self._assert_inside_root(path)
            if path.is_dir():
                path = path / "manifest.json"
            path = self._assert_inside_root(path)
            if path.name != "manifest.json" and path.parent.name not in {"candidates", "rejected"}:
                sibling = path.parent / "manifest.json"
                if sibling.exists():
                    path = sibling
            return path
        value = self._validate_component(raw, label="candidate version")
        # Candidate directories are keyed by ``proposal_id`` for human
        # readability, while the CLI addresses immutable artifacts by their
        # independent ``version``. Resolve by the manifest field instead of
        # assuming the two identifiers are identical.
        matches: list[Path] = []
        for manifest_path in sorted(self.paths.candidates.rglob("manifest.json")):
            if manifest_path.parent.name == "overlay":
                continue
            try:
                manifest_path = self._assert_inside_root(manifest_path)
                if not manifest_path.is_file() or manifest_path.stat().st_size > 1 * 1024 * 1024:
                    continue
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(manifest, dict) and manifest.get("version") == value:
                matches.append(manifest_path)
        if len(matches) > 1:
            raise EvolutionValidationError(f"candidate version is ambiguous: {value}")
        if matches:
            return matches[0]
        legacy = self.paths.candidates / f"{value}.json"
        if legacy.exists():
            return legacy
        raise FileNotFoundError(version_or_path)

    def _load_manifest_candidate(self, path: Path) -> dict[str, Any]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise EvolutionValidationError(f"candidate manifest is not an object: {path}")
        if path.name == "manifest.json" and path.parent.parent == self.paths.candidates:
            # This is a legacy flat candidate named manifest.json in an old
            # manually-created directory; treat it as its own JSON artifact.
            return prepare_candidate(raw, allow_legacy_source_overlay=True)
        if path.name != "manifest.json" or "overlay" not in raw:
            return prepare_candidate(raw, allow_legacy_source_overlay=True)
        candidate = {key: value for key, value in raw.items() if key in _ALLOWED_TOP_LEVEL}
        reflection_path = path.parent / "reflection.json"
        root_cause_path = path.parent / "root-cause.md"
        tests_path = path.parent / "tests" / "cases.json"
        if reflection_path.exists():
            candidate["reflection"] = json.loads(reflection_path.read_text(encoding="utf-8"))
        if root_cause_path.exists():
            candidate["root_cause_md"] = root_cause_path.read_text(encoding="utf-8")
        if tests_path.exists():
            candidate["tests"] = json.loads(tests_path.read_text(encoding="utf-8"))
        return prepare_candidate(candidate, allow_legacy_source_overlay=True)

    def _load_active_manifest(self, version: str) -> dict[str, Any]:
        if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
            raise EvolutionValidationError("active version is invalid")
        manifest_path = self._assert_inside_root(self.paths.active / version / "manifest.json")
        if not manifest_path.exists() or not manifest_path.is_file():
            raise EvolutionValidationError("active pointer has no immutable version manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("status") != "activated":
            raise EvolutionValidationError("active version manifest is invalid")
        manifest_overlay = manifest.get("overlay")
        manifest_hash = manifest.get("overlay_sha256")
        if (
            not isinstance(manifest_overlay, dict)
            or not isinstance(manifest_hash, str)
            or not _SHA256_RE.fullmatch(manifest_hash)
            or manifest_hash != _sha256(manifest_overlay)
        ):
            raise EvolutionValidationError("active version manifest overlay digest is invalid")
        return manifest

    def load_active(self) -> dict[str, Any]:
        path = self.paths.active_json if self.paths.active_json.exists() else self.paths.legacy_active_json
        if not path.exists():
            return {"schema_version": 2, "version": "baseline", "proposal_id": "baseline", "overlay": {}, "activated_at": None}
        path = self._assert_inside_root(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise EvolutionValidationError("active pointer must be an object")
        if isinstance(raw, dict) and str(raw.get("version", "baseline")) == "baseline":
            if raw.get("proposal_id", "baseline") != "baseline" or raw.get("overlay", {}) != {}:
                raise EvolutionValidationError("baseline active pointer is not canonical")
            if "active_manifest_sha256" in raw:
                raise EvolutionValidationError("baseline active pointer cannot reference an active manifest")
            return {
                "schema_version": 2,
                "version": "baseline",
                "proposal_id": "baseline",
                "overlay": {},
                "activated_at": raw.get("activated_at"),
                "effective_on": raw.get("effective_on", "next_collection_run"),
            }
        version = raw.get("version")
        if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
            raise EvolutionValidationError("active pointer version is invalid")
        if raw.get("status") != "active":
            raise EvolutionValidationError("active pointer status is invalid")
        previous_version = raw.get("previous_version", "baseline")
        if not isinstance(previous_version, str) or (
            previous_version != "baseline" and not _VERSION_RE.fullmatch(previous_version)
        ):
            raise EvolutionValidationError("active pointer previous_version is invalid")
        manifest = self._load_active_manifest(version)
        manifest_overlay = manifest.get("overlay")
        manifest_hash = manifest.get("overlay_sha256")
        pointer_hash = raw.get("overlay_sha256")
        if (
            not isinstance(manifest_overlay, dict)
            or not isinstance(manifest_hash, str)
            or not _SHA256_RE.fullmatch(manifest_hash)
            or manifest_hash != _sha256(manifest_overlay)
            or pointer_hash != manifest_hash
        ):
            raise EvolutionValidationError("active overlay digest does not match its immutable manifest")
        manifest_digest = raw.get("active_manifest_sha256")
        if not isinstance(manifest_digest, str) or not _SHA256_RE.fullmatch(manifest_digest):
            raise EvolutionValidationError("active pointer manifest digest is missing or invalid")
        if manifest_digest != _sha256(manifest):
            raise EvolutionValidationError("active version manifest integrity digest is invalid")
        # Candidate content is immutable in the version directory.  Runtime
        # pointer fields may change during rollback, but identity, overlay,
        # evidence, and policy fields must remain byte-for-byte equivalent.
        mutable = {"status", "activated_at", "previous_version", "effective_on", "shadow_report", "active_manifest_sha256"}
        pointer_static = {key: value for key, value in raw.items() if key not in mutable}
        manifest_static = {key: value for key, value in manifest.items() if key not in mutable}
        if pointer_static != manifest_static:
            raise EvolutionValidationError("active pointer does not match its immutable version manifest")
        candidate = {key: value for key, value in raw.items() if key != "active_manifest_sha256"}
        prepared = prepare_candidate(candidate, allow_legacy_source_overlay=True)
        prepared["active_manifest_sha256"] = manifest_digest
        return prepared

    def status(self) -> dict[str, Any]:
        active = self.load_active()
        return {
            "root": str(self.paths.root),
            "active_version": active.get("version", "baseline"),
            "candidate_count": len([path for path in self.paths.candidates.rglob("manifest.json") if path.parent.name != "overlay"])
            + len(list(self.paths.candidates.glob("*.json"))),
            "shadow_count": len(list(self.paths.shadow.rglob("report.json"))) + len(list(self.paths.shadow.glob("*.json"))),
            "history_count": len(list(self.paths.history.glob("*.json"))),
            "rejected_count": len(list(self.paths.rejected.rglob("manifest.json"))) + len(list(self.paths.rejected.glob("*.json"))),
        }

    def save_candidate(self, candidate: dict[str, Any]) -> Path:
        prepared = prepare_candidate(candidate)
        directory = self._candidate_dir(prepared)
        if directory.exists():
            raise FileExistsError(directory)
        overlay_dir = directory / "overlay"
        tests_dir = directory / "tests"
        overlay_dir.mkdir(parents=True, exist_ok=False)
        tests_dir.mkdir(parents=True, exist_ok=False)
        _safe_write_json(directory / "reflection.json", {
            **prepared["reflection"],
            "proposal_id": prepared["proposal_id"],
            "created_at": prepared["created_at"],
            "overlay_sha256": prepared["overlay_sha256"],
        }, exclusive=True)
        _safe_write_text(directory / "root-cause.md", prepared["root_cause_md"], exclusive=True)
        for root, value in prepared["overlay"].items():
            _safe_write_json(overlay_dir / f"{root}.json", value, exclusive=True)
        _safe_write_json(overlay_dir / "manifest.json", {
            "schema_version": 1,
            "roots": sorted(prepared["overlay"]),
            "overlay_sha256": prepared["overlay_sha256"],
        }, exclusive=True)
        _safe_write_json(tests_dir / "cases.json", prepared["tests"], exclusive=True)
        manifest = {
            "schema_version": 2,
            "version": prepared["version"],
            "proposal_id": prepared["proposal_id"],
            "candidate_date": prepared["candidate_date"],
            "created_at": prepared["created_at"],
            "status": "candidate",
            "metadata": prepared.get("metadata", {}),
            "overlay": prepared["overlay"],
            "overlay_sha256": prepared["overlay_sha256"],
            "generality": prepared.get("generality", {}),
            "expected_metric": prepared["expected_metric"],
            "requires_human_change": bool(prepared.get("requires_human_change")),
            "reflection_file": "reflection.json",
            "root_cause_file": "root-cause.md",
            "overlay_dir": "overlay",
            "tests_dir": "tests",
        }
        _safe_write_json(directory / "manifest.json", manifest, exclusive=True)
        return directory / "manifest.json"

    def load_candidate(self, version_or_path: str | Path) -> dict[str, Any]:
        return self._load_manifest_candidate(self._resolve_candidate_path(version_or_path))

    def reject(self, candidate: dict[str, Any], reason: str) -> Path:
        safe_reason = _safe_rejection_reason(reason)
        try:
            prepared = prepare_candidate(candidate)
        except EvolutionValidationError:
            digest = hashlib.sha256(safe_reason.encode("utf-8")).hexdigest()[:12]
            raw_version = candidate.get("version") if isinstance(candidate, dict) else None
            version = raw_version.strip() if isinstance(raw_version, str) else ""
            if not _VERSION_RE.fullmatch(version):
                version = f"rejected-{digest}"
            raw_proposal = candidate.get("proposal_id") if isinstance(candidate, dict) else None
            proposal_id = raw_proposal.strip() if isinstance(raw_proposal, str) else ""
            if not _VERSION_RE.fullmatch(proposal_id):
                proposal_id = version
            raw_date = candidate.get("candidate_date") if isinstance(candidate, dict) else None
            candidate_date = raw_date if isinstance(raw_date, str) and _ISO_DATE_RE.fullmatch(raw_date) else ""
            if candidate_date:
                try:
                    date.fromisoformat(candidate_date)
                except ValueError:
                    candidate_date = ""
            if not candidate_date:
                candidate_date = utc_now()[:10]
            prepared = {
                "version": version,
                "proposal_id": proposal_id,
                "candidate_date": candidate_date,
                "overlay": {},
                "overlay_sha256": _sha256({}),
            }
        directory = self.paths.rejected / prepared["candidate_date"] / _candidate_source_name(prepared)
        directory.mkdir(parents=True, exist_ok=True)
        _safe_write_json(directory / "manifest.json", {
            "schema_version": 2,
            "version": prepared["version"],
            "proposal_id": prepared["proposal_id"],
            "candidate_date": prepared["candidate_date"],
            "status": "rejected",
            "reason": safe_reason,
            "overlay_sha256": prepared["overlay_sha256"],
        })
        return directory / "manifest.json"

    @staticmethod
    def _default_shadow_cases() -> list[dict[str, Any]]:
        return [
            {"name": "positive-security", "kind": "positive", "independent": True, "plan": {"queries": ["security"]}},
            {"name": "positive-prompt-injection", "kind": "positive", "independent": True, "plan": {"queries": ["prompt injection"]}},
            {"name": "positive-backdoor", "kind": "positive", "independent": True, "plan": {"queries": ["backdoor"]}},
            {"name": "negative-facts-invariant", "kind": "negative", "plan": {"queries": ["privacy"]}, "invariant_proof": "facts are not an overlay input"},
        ]

    def _recent_run_replay(self, overlay: dict[str, Any] | None = None) -> dict[str, Any]:
        candidates = [
            self.paths.root / "fixtures" / "recent-run.json",
            self.paths.root / "recent-run.json",
        ]
        digest_files = sorted((config.PROJECT_ROOT / "digests").glob("*/facts.json"))
        candidates.extend(digest_files[-1:])
        for path in candidates:
            if not path.exists():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("recent-run fixture must be an object")
                baseline = value.get("baseline", value)
                if not isinstance(baseline, dict):
                    raise ValueError("recent-run baseline must be an object")
                facts = baseline.get("facts", baseline)
                if not isinstance(facts, dict) or not isinstance(facts.get("papers"), list):
                    raise ValueError("recent-run fixture must contain a facts.papers array")
                paper_ids: set[str] = set()
                normalized_papers: list[dict[str, Any]] = []
                fact_fields = FACT_FIELDS | {"paper_id", "source", "source_id"}
                for index, raw_paper in enumerate(facts["papers"]):
                    if not isinstance(raw_paper, dict):
                        raise ValueError(f"recent-run paper {index} must be an object")
                    paper = PaperFacts.from_dict(raw_paper)
                    paper.validate_discovered()
                    if paper.paper_id in paper_ids:
                        raise ValueError(f"recent-run paper {index} has duplicate paper_id")
                    paper_ids.add(paper.paper_id)
                    normalized_papers.append({key: paper.to_dict().get(key) for key in sorted(fact_fields)})
                normalized_facts = {
                    "schema_version": facts.get("schema_version", 1),
                    "target": facts.get("target"),
                    "papers": normalized_papers,
                }
                before = _sha256(normalized_facts)
                replayed_facts = copy.deepcopy(normalized_facts)
                if before != _sha256(replayed_facts):
                    raise ValueError("recent-run facts changed during replay")
                plan = baseline.get("plan")
                plan_digest = None
                baseline_plan_digest = None
                overlay_plan_changed = False
                if plan is not None:
                    if not isinstance(plan, dict):
                        raise ValueError("recent-run plan must be an object")
                    allowed = set(SearchPlan.__dataclass_fields__)
                    unknown = set(plan) - allowed
                    if unknown:
                        raise ValueError(f"recent-run plan has unknown fields: {sorted(unknown)}")
                    replay_plan = SearchPlan(**plan)
                    replay_plan.validate()
                    baseline_plan = apply_overlay(replay_plan, {})
                    replayed_plan = apply_overlay(replay_plan, overlay or {})
                    if asdict(baseline_plan) != asdict(replay_plan):
                        raise ValueError("baseline replay changed the search plan")
                    plan_digest = _sha256(asdict(replayed_plan))
                    baseline_plan_digest = _sha256(asdict(baseline_plan))
                    overlay_plan_changed = asdict(replayed_plan) != asdict(baseline_plan)
                return {
                    "status": "passed",
                    "fixture": str(path),
                    "papers": len(normalized_papers),
                    "facts_sha256": before,
                    "plan_sha256": plan_digest,
                    "baseline_plan_sha256": baseline_plan_digest,
                    "overlay_plan_changed": overlay_plan_changed,
                    "replayed": True,
                }
            except Exception as exc:
                return {"status": "failed", "fixture": str(path), "error_type": type(exc).__name__, "message": str(exc)[:300]}
        return {"status": "failed", "reason": "no recent-run fixture available"}

    @staticmethod
    def _baseline_policy_tests() -> dict[str, Any]:
        bad_candidates = [
            ("fact", {"overlay": {"search_plan": {"title": "one paper"}}}),
            ("paper_id", {"overlay": {"reconciliation": {"paper_id": "one-paper"}}}),
            ("doi", {"overlay": {"search_plan": {"queries_add": ["10.1234/example"]}}}),
            ("http", {"overlay": {"search_plan": {"queries_add": ["https://example.invalid"]}}}),
            ("http_policy", {"overlay": {"source_policy": {"endpoint": "https://example.invalid"}}}),
            ("materialize", {"overlay": {"reconciliation": {"materialize": True}}}),
            ("secret", {"overlay": {"prompt": {"fragments_add": ["Authorization: Bearer abc123"]}}}),
        ]
        failures = []
        cases = []
        for name, candidate in bad_candidates:
            try:
                validate_overlay(candidate)
            except EvolutionValidationError:
                cases.append({"name": name, "status": "rejected"})
                continue
            cases.append({"name": name, "status": "accepted"})
            failures.append(name)
        return {
            "status": "passed" if not failures else "failed",
            "cases": cases,
            "rejected": len(bad_candidates) - len(failures),
            "failures": failures,
        }

    def shadow(self, candidate: dict[str, Any], fixtures: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
        prepared = prepare_candidate(candidate)
        if fixtures is not None:
            raise EvolutionValidationError("shadow fixtures must be loaded from the candidate")
        # Candidate-owned fixtures are mandatory.  Defaults would let a
        # proposal pass without demonstrating the failure it claims to fix.
        cases = copy.deepcopy(prepared["tests"])
        runner = EvolutionRunner()
        results: list[dict[str, Any]] = []
        trigger = 0
        trigger_effective = 0
        positive = 0
        independent_positive_plans: set[str] = set()
        independent_positive_dimensions: set[str] = set()
        negative = 0
        fact_mutations = 0
        query_plan_changes = 0
        baseline_query_plan_changes = 0
        for index, fixture in enumerate(cases):
            name = fixture.get("name", f"case-{index + 1}") if isinstance(fixture, dict) else f"case-{index + 1}"
            kind = fixture.get("kind", "negative" if "negative" in str(name).casefold() else "positive") if isinstance(fixture, dict) else "positive"
            try:
                if not isinstance(fixture, dict):
                    raise EvolutionValidationError("shadow case must be an object")
                if kind not in {"trigger", "positive", "negative"}:
                    raise EvolutionValidationError("shadow case kind must be trigger, positive, or negative")
                raw_plan = fixture.get("plan")
                if not isinstance(raw_plan, dict):
                    raise EvolutionValidationError("shadow case plan must be an object")
                base = SearchPlan(queries=list(raw_plan.get("queries") or ["security"]), **{
                    key: value for key, value in raw_plan.items() if key != "queries" and key in SearchPlan.__dataclass_fields__
                })
                baseline_response = runner.run(base, {})
                baseline_updated = SearchPlan(**baseline_response["strategy"]["plan"])
                runner_response = runner.run(base, prepared["overlay"])
                updated = SearchPlan(**runner_response["strategy"]["plan"])
                updated.validate()
                plan_changed = asdict(updated) != asdict(base)
                baseline_changed = asdict(baseline_updated) != asdict(base)
                query_plan_changes += int(plan_changed)
                baseline_query_plan_changes += int(baseline_changed)
                if "facts" in fixture:
                    before_facts = _canonical_json(fixture["facts"])
                    after_facts = _canonical_json(copy.deepcopy(fixture["facts"]))
                    if before_facts != after_facts:
                        fact_mutations += 1
                        raise EvolutionValidationError("overlay changed facts")
                if kind == "trigger":
                    trigger += 1
                    trigger_effective += int(plan_changed or bool(runner_response.get("prompt_fragments")) or bool(runner_response.get("reading_skill_fragments")))
                elif kind == "negative":
                    negative += 1
                else:
                    positive += 1
                    # Every distinct positive replay plan is an independent
                    # generality example unless the fixture explicitly marks
                    # it as a duplicate.  Older fixtures omitted the flag;
                    # treating those as non-independent made valid proposals
                    # fail silently.
                    if fixture.get("independent") is not False:
                        independent_positive_plans.add(_sha256(raw_plan))
                        dimensions = fixture.get("dimensions")
                        if isinstance(dimensions, dict):
                            independent_positive_dimensions.add(_canonical_json(dimensions))
                results.append({
                    "name": name,
                    "kind": kind,
                    "status": "passed",
                    "queries": len(updated.queries),
                    "baseline_plan_sha256": _sha256(asdict(base)),
                    "overlay_plan_sha256": _sha256(asdict(updated)),
                    "baseline_plan_changed": baseline_changed,
                    "runner_schema_version": runner_response.get("schema_version"),
                    "plan_changed": plan_changed,
                })
            except Exception as exc:
                results.append({"name": name, "kind": kind, "status": "failed", "error_type": type(exc).__name__, "message": str(exc)[:300]})
        baseline = {"status": "passed" if all(item["status"] == "passed" for item in results) else "failed", "cases": len(results)}
        policy = self._baseline_policy_tests()
        history = replay_history(self.paths.root)
        recent = self._recent_run_replay(prepared["overlay"])
        invariant_proof = bool(str(prepared.get("generality", {}).get("invariant_proof", "")).strip()) or any(
            isinstance(case, dict) and isinstance(case.get("invariant_proof"), str) and case.get("invariant_proof", "").strip() for case in cases
        )
        independent_positive = len(independent_positive_plans)
        generality_status = trigger >= 1 and independent_positive >= 2 and negative >= 1
        expected_metric = prepared["expected_metric"]
        metric_name = expected_metric["name"]
        baseline_metric_values = {
            "query_plan_changes": baseline_query_plan_changes,
            "positive_cases": positive,
            "positive_independent_cases": independent_positive,
            "negative_cases": negative,
            "runner_failures": sum(item["status"] != "passed" for item in results),
            "fact_mutations": fact_mutations,
        }
        overlay_metric_values = {
            "query_plan_changes": query_plan_changes,
            "positive_cases": positive,
            "positive_independent_cases": independent_positive,
            "negative_cases": negative,
            "runner_failures": sum(item["status"] != "passed" for item in results),
            "fact_mutations": fact_mutations,
        }
        baseline_metric = baseline_metric_values[metric_name]
        overlay_metric = overlay_metric_values[metric_name]
        metric_delta = overlay_metric - baseline_metric
        metric_direction = expected_metric["direction"]
        minimum_delta = expected_metric["minimum_delta"]
        metric_passed = {
            "increase": metric_delta >= minimum_delta,
            "decrease": metric_delta <= -minimum_delta,
            "non_decrease": metric_delta >= 0,
            "non_increase": metric_delta <= 0,
            "unchanged": metric_delta == 0,
        }[metric_direction]
        metrics = {
            "positive_cases": positive,
            "positive_independent_cases": independent_positive,
            "positive_independent_dimensions": len(independent_positive_dimensions),
            "trigger_cases": trigger,
            "trigger_effective": trigger_effective,
            "negative_cases": negative,
            "invariant_proof": invariant_proof,
            "baseline_tests": baseline,
            "history_replay": history,
            "recent_run_replay": recent,
            "protected_policy": policy,
            "fact_mutations": fact_mutations,
            "query_plan_changes": query_plan_changes,
            "overlay_effective": bool(prepared["overlay"]),
            "errors": sum(item["status"] != "passed" for item in results),
            "runner_failures": sum(item["status"] != "passed" for item in results),
            "expected_metric": {
                **expected_metric,
                "baseline_value": baseline_metric,
                "overlay_value": overlay_metric,
                "delta": metric_delta,
                "status": "passed" if metric_passed else "failed",
            },
            "requires_human_change": bool(prepared.get("requires_human_change")),
        }
        passed = (
            baseline["status"] == "passed"
            and policy["status"] == "passed"
            and history.get("status") == "passed"
            and recent.get("status") == "passed"
            and generality_status
            and trigger_effective >= 1
            and metrics["fact_mutations"] == 0
            and metrics["runner_failures"] == 0
            and metric_passed
            and not prepared.get("requires_human_change", False)
            and metrics["overlay_effective"]
            and (
                query_plan_changes > 0
                or any(root in prepared["overlay"] and prepared["overlay"].get(root) for root in ("prompt", "reading_skill"))
            )
        )
        report_path = self._shadow_report_path(prepared)
        runtime = _baseline_runtime_metadata()
        runtime.update({
            "overlay_version": prepared["version"],
            "overlay_sha256": prepared["overlay_sha256"],
        })
        report = {
            "schema_version": 2,
            "candidate_version": prepared["version"],
            "proposal_id": prepared["proposal_id"],
            "candidate_date": prepared["candidate_date"],
            "candidate_overlay_sha256": prepared["overlay_sha256"],
            "candidate_tests_sha256": _sha256(prepared["tests"]),
            "status": "passed" if passed else "failed",
            "generated_at": utc_now(),
            "report_path": str(report_path),
            "runtime": runtime,
            "generality": {
                "status": "passed" if generality_status else "failed",
                "positive_independent_cases": independent_positive,
                "positive_cases": positive,
                "negative_cases": negative,
                "trigger_cases": trigger,
                "trigger_effective": trigger_effective,
                "invariant_proof": invariant_proof,
            },
            "metrics": metrics,
            "cases": results,
        }
        report["report_sha256"] = _sha256(report)
        _safe_write_json(report_path, report)
        return report

    def _event(self, event: dict[str, Any]) -> Path:
        sequence = len(list(self.paths.history.glob("*.json"))) + 1
        event = {**event, "schema_version": 2, "sequence": sequence, "event_at": utc_now()}
        digest = hashlib.sha256(_canonical_json(event).encode("utf-8")).hexdigest()[:16]
        path = self.paths.history / f"{sequence:08d}-{digest}.json"
        _safe_write_json(path, event, exclusive=True)
        return path

    def _failed_overlay_hashes(self) -> set[str]:
        hashes: set[str] = set()
        for path in self.paths.history.glob("*.json"):
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if event.get("event") == "health_check_failed" and event.get("overlay_sha256"):
                hashes.add(str(event["overlay_sha256"]))
        return hashes

    def _disable_active_to_baseline(self, *, from_version: str, reason: str) -> dict[str, Any]:
        """Replace an unreadable active pointer with the canonical baseline."""
        baseline = {
            "schema_version": 2,
            "version": "baseline",
            "proposal_id": "baseline",
            "overlay": {},
            "activated_at": utc_now(),
            "effective_on": "next_collection_run",
        }
        _safe_write_json(self.paths.legacy_active_json, baseline)
        _safe_write_json(self.paths.active_json, baseline)
        self._event({
            "event": "rollback",
            "version": "baseline",
            "from_version": from_version,
            "reason": _safe_rejection_reason(reason),
        })
        return {
            "status": "rolled_back",
            "version": "baseline",
            "from_version": from_version,
            "effective_on": "next_collection_run",
        }

    def health_check(self) -> dict[str, Any]:
        """Validate the selected overlay before a collection run.

        A failed hash is never retried automatically.  The previous stable
        pointer is restored and both the failure and rollback are recorded in
        the history directory, leaving baseline collection available.
        """
        try:
            active = self.load_active()
        except Exception as exc:
            reason = _safe_rejection_reason(f"{type(exc).__name__}: {exc}")
            self._event({
                "event": "health_check_failed",
                "version": "unknown",
                "reason": reason,
                "retry_suppressed": True,
                "load_failed": True,
            })
            rollback = self._disable_active_to_baseline(from_version="unknown", reason=reason)
            return {
                "status": "rolled_back",
                "version": "unknown",
                "reason": reason,
                "rollback": rollback,
            }
        version = str(active.get("version", "baseline"))
        overlay = active.get("overlay") or {}
        if version == "baseline" or not overlay:
            return {"status": "passed", "version": "baseline", "effective_on": "current_run"}
        overlay_hash = str(active.get("overlay_sha256") or _sha256(overlay))
        if overlay_hash in self._failed_overlay_hashes():
            reason = "overlay hash previously failed health check; retry suppressed"
            self._event({"event": "health_check_failed", "version": version, "overlay_sha256": overlay_hash, "reason": reason, "retry_suppressed": True})
            rollback = self.rollback()
            return {"status": "rolled_back", "version": version, "reason": reason, "rollback": rollback}
        try:
            response = EvolutionRunner().run(SearchPlan(queries=["health-check"]), overlay)
            _runner_output(response)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:300]}"
            self._event({"event": "health_check_failed", "version": version, "overlay_sha256": overlay_hash, "reason": reason, "retry_suppressed": True})
            rollback = self.rollback()
            return {"status": "rolled_back", "version": version, "reason": reason, "rollback": rollback}
        return {"status": "passed", "version": version, "overlay_sha256": overlay_hash, "effective_on": "current_run"}

    def _matching_shadow_report(self, prepared: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(report, dict):
            raise EvolutionValidationError("a persisted shadow report is required")
        expected_path = self._shadow_report_path(prepared)
        report_path = Path(str(report.get("report_path", ""))).resolve()
        if report_path != expected_path.resolve() or not expected_path.exists():
            raise EvolutionValidationError("shadow report is not the persisted report for this candidate")
        persisted = json.loads(expected_path.read_text(encoding="utf-8"))
        if persisted != report:
            raise EvolutionValidationError("shadow report does not match its persisted bytes")
        if report.get("candidate_version") != prepared["version"] or report.get("proposal_id") != prepared["proposal_id"]:
            raise EvolutionValidationError("shadow report candidate identity does not match")
        if report.get("candidate_overlay_sha256") != prepared["overlay_sha256"]:
            raise EvolutionValidationError("shadow report overlay digest does not match")
        if report.get("candidate_tests_sha256") != _sha256(prepared["tests"]):
            raise EvolutionValidationError("shadow report fixtures do not match the candidate")
        runtime = report.get("runtime")
        if not isinstance(runtime, dict) or runtime.get("overlay_version") != prepared["version"] or runtime.get("overlay_sha256") != prepared["overlay_sha256"]:
            raise EvolutionValidationError("shadow report runtime identity is incomplete")
        if report.get("status") != "passed" or report.get("generality", {}).get("status") != "passed":
            raise EvolutionValidationError("cannot activate a failed shadow report")
        if report.get("metrics", {}).get("requires_human_change"):
            raise EvolutionValidationError("candidate requires_human_change=true and cannot be activated")
        generality = report.get("generality") or {}
        if int(generality.get("trigger_cases", 0)) < 1 or int(generality.get("trigger_effective", 0)) < 1:
            raise EvolutionValidationError("shadow report has no effective trigger fixture")
        if int(generality.get("positive_independent_cases", 0)) < 2:
            raise EvolutionValidationError("shadow report has fewer than two independent positive cases")
        if int(generality.get("negative_cases", 0)) < 1:
            raise EvolutionValidationError("shadow report has no negative case")
        metrics = report.get("metrics") or {}
        if metrics.get("positive_independent_cases") != generality.get("positive_independent_cases"):
            raise EvolutionValidationError("shadow report generality metrics disagree")
        if metrics.get("fact_mutations") != 0 or metrics.get("errors") != 0:
            raise EvolutionValidationError("shadow metrics reported fact mutation")
        if metrics.get("expected_metric", {}).get("status") != "passed":
            raise EvolutionValidationError("shadow report expected_metric did not improve")
        for key in ("baseline_tests", "history_replay", "recent_run_replay", "protected_policy"):
            if metrics.get(key, {}).get("status") != "passed":
                raise EvolutionValidationError(f"shadow report failed required gate: {key}")
        if report.get("report_sha256") != _sha256({key: value for key, value in report.items() if key != "report_sha256"}):
            raise EvolutionValidationError("shadow report integrity digest is invalid")
        return persisted

    def activate(self, candidate: dict[str, Any] | str | Path, *, report: dict[str, Any] | None = None) -> dict[str, Any]:
        if report is None:
            raise EvolutionValidationError("activation requires a persisted matching shadow report")
        prepared = self.load_candidate(candidate) if isinstance(candidate, (str, Path)) else prepare_candidate(candidate)
        candidate_path = self._resolve_candidate_path(prepared["version"])
        persisted_candidate = self.load_candidate(candidate_path)
        if persisted_candidate["overlay_sha256"] != prepared["overlay_sha256"]:
            raise EvolutionValidationError("candidate is not the persisted candidate under review")
        self._matching_shadow_report(prepared, report)
        previous = self.load_active().get("version", "baseline")
        activated_at = utc_now()
        active = {
            **prepared,
            "status": "active",
            "activated_at": activated_at,
            "previous_version": previous,
            "effective_on": "next_collection_run",
        }
        # Keep the legacy root file readable for old installations, while the
        # active directory is the canonical current-state location.
        active_dir = self.paths.active / prepared["version"]
        if active_dir.exists():
            raise EvolutionValidationError("active version is immutable and already exists")
        # Keep a self-contained immutable copy of the approved candidate in
        # the active version directory.  The root pointer is still the atomic
        # selector, but recovery/replay does not depend on mutable candidates.
        active_manifest = {
            **active,
            "status": "activated",
            "shadow_report": report["report_path"],
        }
        active["active_manifest_sha256"] = _sha256(active_manifest)
        _safe_write_json(active_dir / "manifest.json", active_manifest)
        # Publish the canonical pointer last. A failure before this write leaves
        # the previous active version selected by load_active().
        _safe_write_json(self.paths.legacy_active_json, active)
        _safe_write_json(self.paths.active_json, active)
        self._event({
            "event": "activate",
            "version": prepared["version"],
            "previous_version": previous,
            "shadow_report": report["report_path"],
            "baseline_commit": report.get("runtime", {}).get("baseline_commit"),
            "overlay_sha256": prepared["overlay_sha256"],
            "prompt_version": report.get("runtime", {}).get("prompt_version"),
            "skill_version": report.get("runtime", {}).get("skill_version"),
            "effective_on": "next_collection_run",
        })
        return {"status": "activated", "version": prepared["version"], "previous_version": previous, "effective_on": "next_collection_run"}

    def rollback(self, version: str | None = None) -> dict[str, Any]:
        active = self.load_active()
        target = version or str(active.get("previous_version") or "baseline")
        if target != "baseline":
            target = self._validate_component(target, label="rollback version")
        if target == "baseline":
            restored = {"schema_version": 2, "version": "baseline", "proposal_id": "baseline", "overlay": {}, "activated_at": utc_now(), "effective_on": "next_collection_run"}
        else:
            manifest = self._load_active_manifest(target)
            if manifest.get("version") != target:
                raise EvolutionValidationError("rollback target active manifest identity is invalid")
            manifest_digest = _sha256(manifest)
            restored = {
                key: value
                for key, value in manifest.items()
                if key not in {"status", "shadow_report"}
            }
            restored.update({
                "status": "active",
                "activated_at": utc_now(),
                "previous_version": active.get("version", "baseline"),
                "effective_on": "next_collection_run",
                "active_manifest_sha256": manifest_digest,
            })
        _safe_write_json(self.paths.legacy_active_json, restored)
        _safe_write_json(self.paths.active_json, restored)
        self._event({"event": "rollback", "version": target, "from_version": active.get("version", "baseline")})
        return {"status": "rolled_back", "version": target, "from_version": active.get("version", "baseline"), "effective_on": "next_collection_run"}


def replay_history(root: Path | None = None) -> dict[str, Any]:
    store = EvolutionStore(root)
    active: dict[str, Any] = {"schema_version": 2, "version": "baseline", "proposal_id": "baseline", "overlay": {}}
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for expected_sequence, path in enumerate(sorted(store.paths.history.glob("*.json")), start=1):
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(event, dict):
                raise EvolutionValidationError("history event must be an object")
            if event.get("schema_version") != 2 or event.get("sequence") != expected_sequence:
                raise EvolutionValidationError("history sequence or schema is invalid")
            event_digest = hashlib.sha256(_canonical_json(event).encode("utf-8")).hexdigest()[:16]
            if path.name != f"{expected_sequence:08d}-{event_digest}.json":
                raise EvolutionValidationError("history event filename integrity is invalid")
            events.append(event)
            if event.get("event") == "activate":
                version = event.get("version")
                manifest = store._load_active_manifest(version)
                candidate = prepare_candidate({
                    key: value
                    for key, value in manifest.items()
                    if key not in {"status", "shadow_report"}
                }, allow_legacy_source_overlay=True)
                if event.get("overlay_sha256") != candidate.get("overlay_sha256"):
                    raise EvolutionValidationError("activation history overlay digest does not match immutable manifest")
                report_path = store._assert_inside_root(Path(str(event.get("shadow_report", ""))))
                if not report_path.exists():
                    raise EvolutionValidationError("activation history references a missing shadow report")
                report = json.loads(report_path.read_text(encoding="utf-8"))
                store._matching_shadow_report(candidate, report)
                active = candidate
            elif event.get("event") == "rollback":
                version = event.get("version", "baseline")
                if version == "baseline":
                    active = {"schema_version": 2, "version": "baseline", "proposal_id": "baseline", "overlay": {}}
                else:
                    manifest = store._load_active_manifest(version)
                    active = prepare_candidate({
                        key: value
                        for key, value in manifest.items()
                        if key not in {"status", "shadow_report"}
                    }, allow_legacy_source_overlay=True)
            else:
                raise EvolutionValidationError("unknown history event")
        except Exception as exc:
            errors.append({"path": str(path), "error_type": type(exc).__name__, "message": str(exc)[:300]})
    try:
        persisted = store.load_active()
        if (
            persisted.get("version", "baseline") != active.get("version", "baseline")
            or (
                persisted.get("version", "baseline") != "baseline"
                and persisted.get("overlay_sha256") != active.get("overlay_sha256")
            )
        ):
            errors.append({"path": str(store.paths.active_json), "error_type": "EvolutionValidationError", "message": "active state does not match history replay"})
    except Exception as exc:
        errors.append({"path": str(store.paths.active_json), "error_type": type(exc).__name__, "message": str(exc)[:300]})
    return {"status": "passed" if not errors else "failed", "active": active, "events": len(events), "errors": errors}


def validate_evolution(candidate: dict[str, Any]) -> dict[str, Any]:
    prepared = prepare_candidate(candidate)
    return {
        "status": "valid",
        "version": prepared["version"],
        "proposal_id": prepared["proposal_id"],
        "overlay_keys": sorted(prepared["overlay"]),
        "expected_metric": prepared["expected_metric"],
        "requires_human_change": bool(prepared.get("requires_human_change")),
        "required_shadow_gates": ["baseline_tests", "history_replay", "recent_run_replay", "generality", "protected_policy", "expected_metric"],
    }


def reflect(candidate: dict[str, Any], root: Path | None = None) -> Path:
    return EvolutionStore(root).save_candidate(candidate)


def shadow_evolution(candidate: dict[str, Any], root: Path | None = None, fixtures: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    return EvolutionStore(root).shadow(candidate, fixtures)


def activate_evolution(candidate: dict[str, Any] | str | Path, root: Path | None = None, report: dict[str, Any] | None = None) -> dict[str, Any]:
    return EvolutionStore(root).activate(candidate, report=report)


def rollback_evolution(version: str | None = None, root: Path | None = None) -> dict[str, Any]:
    return EvolutionStore(root).rollback(version)


def evolution_status(root: Path | None = None) -> dict[str, Any]:
    return EvolutionStore(root).status()


def _overlay_worker_main() -> int:
    """Subprocess entry point; stdout is a strict JSON-only protocol."""
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict) or request.get("schema_version") != RUNNER_SCHEMA_VERSION:
            raise EvolutionRunnerError("overlay worker request schema is invalid")
        raw_plan = request.get("plan")
        if not isinstance(raw_plan, dict):
            raise EvolutionRunnerError("overlay worker request plan is invalid")
        plan = SearchPlan(**raw_plan)
        plan.validate()
        overlay = request.get("overlay")
        if not isinstance(overlay, dict):
            raise EvolutionRunnerError("overlay worker request overlay is invalid")
        source_fixtures = request.get("source_fixtures", [])
        if not isinstance(source_fixtures, list) or len(source_fixtures) > _SOURCE_REPORT_MAX_ITEMS:
            raise EvolutionRunnerError("overlay worker source_fixtures are invalid")
        source_reports = request.get("source_reports", [])
        _validate_source_reports(source_reports)
        # Validation and materialization remain parent-process baseline work;
        # the worker computes only declarative strategy output.
        validated = validate_overlay({"overlay": overlay})["overlay"]
        updated = apply_overlay(plan, validated)
        parsed_reports = list(source_reports)
        for fixture in source_fixtures:
            if not isinstance(fixture, dict):
                raise EvolutionRunnerError("overlay worker source fixture must be an object")
            parsed_reports.append(_parse_source_fixture(fixture))
        output = {
            "schema_version": RUNNER_SCHEMA_VERSION,
            "strategy": {"plan": asdict(updated)},
            "raw_candidates": [],
            "evidence": [],
            "prompt_fragments": prompt_fragments(overlay=validated),
            "reading_skill_fragments": reading_skill_fragments(overlay=validated),
            "source_reports": parsed_reports,
        }
        sys.stdout.write(_canonical_json(_runner_output(output)))
        return 0
    except Exception as exc:
        # Keep errors on stderr so stdout can never be mistaken for a valid
        # strategy response by the baseline parent.
        print(f"overlay worker error: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    if "--overlay-worker" in sys.argv[1:]:
        raise SystemExit(_overlay_worker_main())
