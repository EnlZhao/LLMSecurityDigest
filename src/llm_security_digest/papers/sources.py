from __future__ import annotations

import html
import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib import parse

from .http import HttpClient, HttpRequestError, HttpResponse
from .models import (
    DiscoveryResult,
    PaperFacts,
    SearchPlan,
    get_venue_spec,
    normalize_doi,
    normalize_title,
    utc_now,
    VENUE_SPECS,
    venue_specs_for_group,
)
from .openreview_client import (
    OpenReviewClientFactory,
    openreview_failure_stage,
    openreview_error_message,
)
from .official import (
    ADAPTERS,
    AAAIOJSAdapter,
    ACLAnthologyAdapter,
    CVFAdapter,
    IJCAIAdapter,
    NDSSAdapter,
    NeurIPSAdapter,
    PMLRAdapter,
    USENIXAdapter,
    parse_official_detail,
)


ARXIV_API_URL = "https://export.arxiv.org/api/query"
# OpenReview API v2. The v1 host remains a compatibility fallback for old
# venue identifiers and installations that have not migrated their gateway.
OPENREVIEW_API_URL = "https://api2.openreview.net/notes"
OPENREVIEW_LEGACY_API_URL = "https://api.openreview.net/notes"
CROSSREF_API_URL = "https://api.crossref.org/works"
SERPAPI_URL = "https://serpapi.com/search.json"
# IEEE exposes a separate Xplore API.  The endpoint and venue mapping are
# baseline-owned; an overlay can never choose a host or publication title.
IEEE_XPLORE_API_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"
IEEE_XPLORE_API_KEY_ENV = "IEEE_XPLORE_API_KEY"
IEEE_XPLORE_VENUES = frozenset({"ieee-sp", "tdsc", "tifs"})


def _v1_submission_invitation(venue_id: str) -> str:
    """Map a registered v2 venue id to the OpenReview v1 invitation id."""
    value = str(venue_id or "").strip().rstrip("/")
    if re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*/(?:19|20)\d{2}/Conference/-/Submission",
        value,
        flags=re.IGNORECASE,
    ):
        return value
    if not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*/(?:19|20)\d{2}/Conference",
        value,
        flags=re.IGNORECASE,
    ):
        raise ValueError(f"invalid OpenReview venue id for v1 invitation: {venue_id!r}")
    return f"{value}/-/Submission"


_OPENREVIEW_RECOVERY_HOSTS = frozenset({"api2.openreview.net"})
_OPENREVIEW_RECOVERY_PARAMS = (
    "content.venueid",
    "limit",
    "offset",
    "details",
    "id",
    "forum",
    "replyto",
)


def _openreview_recovery_url(params: dict[str, str]) -> str:
    """Build the only HTTP URL allowed for v2 challenge recovery."""

    unknown = set(params) - set(_OPENREVIEW_RECOVERY_PARAMS)
    if unknown:
        raise ValueError(f"unsupported OpenReview recovery parameters: {sorted(unknown)}")
    query = [
        (key, str(params[key]))
        for key in _OPENREVIEW_RECOVERY_PARAMS
        if key in params
    ]
    encoded = parse.urlencode(query)
    return f"{OPENREVIEW_API_URL}?{encoded}" if encoded else OPENREVIEW_API_URL

# These are baseline routing rules.  A candidate may carry a landing URL for
# display, but materialization derives the request URL from this table and the
# validated source id instead of trusting candidate-provided URLs.
_OFFICIAL_SOURCE_SPECS = {
    "acl": "acl",
    "emnlp": "emnlp",
    "pmlr": "icml",
    "neurips": "neurips",
    "aaai_ojs": "aaai",
    "ijcai": "ijcai",
    "usenix": "usenix-security",
    "ndss": "ndss",
    "cvpr": "cvpr",
    "eccv": "eccv",
}
_SOURCE_HOSTS = {
    "acl": frozenset({"aclanthology.org"}),
    "emnlp": frozenset({"aclanthology.org"}),
    "pmlr": frozenset({"proceedings.mlr.press", "raw.githubusercontent.com"}),
    "neurips": frozenset({"proceedings.neurips.cc"}),
    "aaai_ojs": frozenset({"ojs.aaai.org"}),
    "ijcai": frozenset({"www.ijcai.org"}),
    "usenix": frozenset({"www.usenix.org"}),
    "ndss": frozenset({"www.ndss-symposium.org"}),
    "cvpr": frozenset({"openaccess.thecvf.com"}),
    "eccv": frozenset({"www.ecva.net"}),
}
_DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)

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
    values: list[str] = []
    for item in value:
        # API v2's unified profile field is commonly a list of objects such
        # as {"fullname": "…", "username": "…"}. Never stringify an
        # object into an apparent author name: doing so corrupts matching.
        if isinstance(item, dict):
            if "value" in item:
                nested = _list(item["value"] if isinstance(item["value"], list) else [item["value"]])
                values.extend(nested)
                continue
            item = item.get("fullname") or item.get("full_name") or item.get("name") or ""
        text = _text(item)
        if text:
            values.append(text)
    return values


def _iso_from_millis(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def _strip_markup(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return " ".join(html.unescape(value).split())


def discovery_query_for_general_index(query: str) -> str:
    """Translate arXiv field prefixes to portable Boolean search text.

    Search plans are shared by arXiv, Crossref, and IEEE Xplore.  The latter
    two accept ordinary bibliographic/Boolean text rather than arXiv's
    ``abs:``/``ti:`` field syntax.  Removing only the registered field prefix
    retains the user's terms and grouping while avoiding source-specific
    syntax being sent verbatim to a different API.
    """
    return re.sub(
        r"(?i)\b(?:abs|ti|au|cat|all):(?=(?:\"|[^\s]))",
        "",
        str(query or ""),
    )


def _provenance(response: HttpResponse, *, source: str) -> dict[str, Any]:
    provenance = {
        "source": source,
        "source_url": response.url,
        "final_url": response.final_url,
        "transport": response.transport,
        "redirect_chain": list(response.redirect_chain),
        "fetched_at": utc_now(),
        "response_sha256": response.sha256,
        "extractor_version": "1",
    }
    if response.provenance:
        provenance["transport_provenance"] = dict(response.provenance)
    return provenance


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


def official_route_for_paper(paper: PaperFacts) -> tuple[str, str, frozenset[str]]:
    """Build a detail URL from a baseline source/id pair.

    This function intentionally ignores ``landing_url`` and arbitrary
    candidate metadata.  The route is constrained by the adapter's source-id
    grammar and a registered host, so a selected JSON artifact cannot turn
    identity refresh into an arbitrary HTTPS fetch.
    """
    source = str(paper.source or "").casefold()
    spec_key = _OFFICIAL_SOURCE_SPECS.get(source)
    hosts = _SOURCE_HOSTS.get(source)
    if not spec_key or not hosts:
        raise ValueError(f"unsupported official source: {paper.source!r}")
    source_id = str(paper.source_id or "").strip()
    if paper.paper_id != f"{paper.source}:{paper.source_id}":
        raise ValueError("paper identity does not match source and source_id")

    if source in {"acl", "emnlp"}:
        if not re.fullmatch(r"(?:19|20)\d{2}\.[a-z0-9-]+\.[1-9]\d*", source_id, re.IGNORECASE):
            raise ValueError("invalid ACL Anthology source id")
        url = f"https://aclanthology.org/{source_id}/"
    elif source == "pmlr":
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*):(v\d+)", source_id, re.IGNORECASE)
        if not match:
            raise ValueError("invalid PMLR source id")
        url = f"https://proceedings.mlr.press/{match.group(2)}/{match.group(1)}.html"
    elif source == "neurips":
        match = re.fullmatch(r"((?:19|20)\d{2}):([A-Za-z0-9][A-Za-z0-9._-]{1,180})", source_id)
        if not match:
            raise ValueError("invalid NeurIPS source id")
        url = f"https://proceedings.neurips.cc/paper_files/paper/{match.group(1)}/{match.group(2)}-Abstract-Conference.html"
    elif source == "aaai_ojs":
        if not re.fullmatch(r"[1-9]\d{0,8}", source_id):
            raise ValueError("invalid AAAI article id")
        url = f"https://ojs.aaai.org/index.php/AAAI/article/view/{source_id}"
    elif source == "ijcai":
        match = re.fullmatch(r"((?:19|20)\d{2})-([1-9]\d{0,4})", source_id)
        if not match:
            raise ValueError("invalid IJCAI source id")
        url = f"https://www.ijcai.org/proceedings/{match.group(1)}/{int(match.group(2))}"
    elif source == "usenix":
        match = re.fullmatch(r"((?:19|20)\d{2}):([A-Za-z0-9][A-Za-z0-9._-]{1,160})", source_id)
        if not match:
            raise ValueError("invalid USENIX presentation id")
        year = int(match.group(1))
        url = f"https://www.usenix.org/conference/usenixsecurity{year % 100:02d}/presentation/{match.group(2)}"
    elif source == "ndss":
        match = re.fullmatch(r"((?:19|20)\d{2}):([A-Za-z0-9][A-Za-z0-9._-]{1,180})", source_id)
        if not match:
            raise ValueError("invalid NDSS source id")
        # Current NDSS detail pages are canonicalized without the symposium
        # year segment; the year remains part of the source id for identity.
        url = f"https://www.ndss-symposium.org/ndss-paper/{match.group(2)}/"
    elif source == "cvpr":
        match = re.fullmatch(r"((?:19|20)\d{2}):([A-Za-z0-9][A-Za-z0-9._-]{1,240})", source_id)
        if not match:
            raise ValueError("invalid CVF source id")
        url = f"https://openaccess.thecvf.com/content/CVPR{match.group(1)}/html/{match.group(2)}.html"
    elif source == "eccv":
        match = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*_ECCV_((?:19|20)\d{2})_paper", source_id)
        if not match:
            raise ValueError("invalid ECVA source id")
        year = int(match.group(1))
        url = f"https://www.ecva.net/papers/eccv_{year}/papers_ECCV/html/{source_id}.php"
    else:  # pragma: no cover - source map is exhaustive by construction.
        raise ValueError(f"unsupported official source: {paper.source!r}")
    return spec_key, url, hosts


def trusted_fulltext_hosts(paper: PaperFacts) -> frozenset[str]:
    """Return baseline-owned hosts allowed for a paper's full text."""
    source = str(paper.source or "").casefold()
    if source == "arxiv":
        return frozenset({"arxiv.org", "export.arxiv.org"})
    if source == "openreview":
        return frozenset({"openreview.net", "api.openreview.net", "api2.openreview.net"})
    if source in _SOURCE_HOSTS:
        return _SOURCE_HOSTS[source]
    if source == "ieee_xplore":
        return frozenset({"ieeexplore.ieee.org"})
    if source == "crossref":
        spec = get_venue_spec(paper.venue)
        if spec is None:
            raise ValueError("Crossref paper has no registered venue")
        return _trusted_crossref_hosts(spec)
    raise ValueError(f"no trusted full-text hosts for source {paper.source!r}")


def trusted_fulltext_url(paper: PaperFacts, url: str) -> bool:
    """Apply the source-specific path restriction for registered PDF hosts."""
    parsed = parse.urlsplit(url)
    if str(paper.source).casefold() != "pmlr" or (parsed.hostname or "").casefold().rstrip(".") != "raw.githubusercontent.com":
        return True
    paper_key, separator, volume = str(paper.source_id or "").rpartition(":")
    return bool(
        separator
        and not parsed.query
        and parsed.username is None
        and parsed.password is None
        and re.fullmatch(
            rf"/mlresearch/{re.escape(volume)}/main/assets/{re.escape(paper_key)}/{re.escape(paper_key)}\.pdf",
            parsed.path,
        )
    )


def _trusted_crossref_hosts(spec: Any) -> frozenset[str]:
    if spec.key in {"ieee-sp", "tdsc", "tifs"}:
            return frozenset({"ieeexplore.ieee.org", "www.ieee-security.org", "www.computer.org"})
    if spec.key in {"acm-ccs", "tops"}:
            return frozenset({"dl.acm.org", "www.sigsac.org"})
    return frozenset()


def _author_identity(value: str) -> str:
    """Normalize an author for conservative cross-source identity checks."""
    value = " ".join(str(value or "").split())
    if not value:
        return ""
    # Formal APIs commonly disagree on ``Family, Given`` vs ``Given Family``.
    if "," in value:
        family, given = value.split(",", 1)
        value = f"{given} {family}"
    return normalize_title(value)


def author_jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    left_set = {_author_identity(value) for value in left if _author_identity(value)}
    right_set = {_author_identity(value) for value in right if _author_identity(value)}
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def formal_duplicate_match(left: PaperFacts, right: PaperFacts) -> tuple[str, float | None] | None:
    """Return the narrow identity proof allowed for formal-source de-duplication.

    This deliberately does not merge metadata. It only lets the caller retain
    its earlier canonical source when the duplicate identity is proven.
    """
    left_doi = normalize_doi(left.doi or left.identifiers.get("doi", ""))
    right_doi = normalize_doi(right.doi or right.identifiers.get("doi", ""))
    if left_doi or right_doi:
        return ("doi_exact", None) if left_doi and left_doi == right_doi else None
    if normalize_title(left.title) != normalize_title(right.title):
        return None
    if not left.authors or not right.authors:
        return None
    if _author_identity(left.authors[0]) != _author_identity(right.authors[0]):
        return None
    similarity = author_jaccard(left.authors, right.authors)
    return ("title_author", similarity) if similarity >= 0.8 else None


def reconcile_arxiv_to_formal(
    arxiv: PaperFacts, formal_records: Iterable[PaperFacts]
) -> tuple[PaperFacts | None, dict[str, Any]]:
    """Return the canonical formal record for an arXiv preprint when exact.

    DOI identity wins. Without a DOI the title must be exact after Unicode
    normalization, the first author must agree, and author Jaccard must be at
    least 0.8. Ambiguous matches stay unresolved instead of guessing.
    """
    if arxiv.source != "arxiv":
        return None, {"state": "not_arxiv"}
    records = [record for record in formal_records if record.source != "arxiv"]
    arxiv_doi = normalize_doi(arxiv.doi or arxiv.identifiers.get("doi", ""))
    if arxiv_doi:
        matches = [
            record for record in records
            if normalize_doi(record.doi or record.identifiers.get("doi", "")) == arxiv_doi
        ]
        if len(matches) == 1:
            return _merge_arxiv_alternate(matches[0], arxiv, state="doi_exact"), {
                "state": "matched",
                "method": "doi_exact",
                "formal_id": matches[0].paper_id,
            }
        if len(matches) > 1:
            return None, {"state": "ambiguous", "method": "doi_exact", "count": len(matches)}
        # A non-matching advertised DOI is evidence against title-only
        # reconciliation; do not silently pair it with a different DOI.
        return None, {"state": "unmatched", "method": "doi_exact", "reason": "doi_not_found"}
    matches = []
    for record in records:
        match = formal_duplicate_match(arxiv, record)
        if match and match[0] == "title_author":
            matches.append((record, match[1] or 0.0))
    if len(matches) == 1:
        record, similarity = matches[0]
        return _merge_arxiv_alternate(record, arxiv, state="title_author"), {
            "state": "matched",
            "method": "title_author",
            "author_jaccard": similarity,
            "formal_id": record.paper_id,
        }
    if len(matches) > 1:
        return None, {"state": "ambiguous", "method": "title_author", "count": len(matches)}
    return None, {"state": "unmatched"}


def _merge_arxiv_alternate(formal: PaperFacts, arxiv: PaperFacts, *, state: str) -> PaperFacts:
    """Copy only alternate identity/provenance data onto formal facts."""
    merged = PaperFacts.from_dict(formal.to_dict())
    merged.collection_tier = "formal"
    merged.match_state = "matched"
    merged.alternate_ids = list(dict.fromkeys([*merged.alternate_ids, arxiv.source_id]))
    merged.alternate_links = dict(merged.alternate_links)
    merged.alternate_links.setdefault("arxiv", arxiv.landing_url)
    merged.identifiers = dict(merged.identifiers)
    merged.identifiers.setdefault("arxiv", arxiv.source_id)
    merged.source_metadata = dict(merged.source_metadata)
    merged.source_metadata["arxiv"] = {
        "id": arxiv.source_id,
        "doi": arxiv.doi,
        "journal_ref": arxiv.source_metadata.get("journal_ref"),
    }
    merged.venue_evidence = [*merged.venue_evidence, *arxiv.venue_evidence]
    return merged


# Public aliases retained for callers that describe the operation as a
# reconciliation record rather than a source-to-source match.
reconcile_arxiv_record = reconcile_arxiv_to_formal
authors_jaccard = author_jaccard


def _reply_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("notes"), list):
            return [item for item in value["notes"] if isinstance(item, dict)]
        return [item for item in value.values() if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


_DECISION_INVITATION_TOKENS = frozenset({
    "decision",
    "acceptance_decision",
    "final_decision",
})


def _invitation_values(value: Any) -> list[str]:
    """Extract invitation ids from v1/v2 string and object shapes."""
    if isinstance(value, (list, tuple, set)):
        result: list[str] = []
        for item in value:
            result.extend(_invitation_values(item))
        return result
    if isinstance(value, dict):
        for key in ("id", "name", "value", "invitation"):
            if key in value:
                return _invitation_values(value[key])
        return []
    text = _text(value).strip()
    return [text] if text else []


def _is_decision_invitation(value: str) -> bool:
    """Recognize final decision invitations, not review recommendations."""
    normalized = value.strip().casefold().rstrip("/")
    if "/-/" not in normalized:
        return False
    token = normalized.rsplit("/-/", 1)[-1].split("?", 1)[0].split("#", 1)[0]
    token = re.sub(r"[^a-z0-9]+", "_", token).strip("_")
    if token in _DECISION_INVITATION_TOKENS:
        return True
    # A few venues add a qualifier to the final decision token. Keep the
    # allow-list narrow so reviewer/meta/track recommendations do not pass.
    return token.endswith("_decision") and token.startswith(("accept", "final", "paper_accept"))


def _is_decision_reply(reply: dict[str, Any]) -> bool:
    """Accept only replies carrying OpenReview's explicit decision invitation."""
    invitations = reply.get("invitations") or reply.get("invitation")
    return any(_is_decision_invitation(value) for value in _invitation_values(invitations))


def _decision_content_values(content: Any) -> list[str]:
    if not isinstance(content, dict):
        return []
    values: list[str] = []
    for key, raw_value in content.items():
        normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
        if normalized not in {
            "decision",
            "recommendation",
            "status",
            "acceptance_decision",
            "final_decision",
        }:
            continue
        value = _text(raw_value)
        if value:
            values.append(value)
    return values


def _openreview_decisions(note: dict[str, Any]) -> list[str]:
    """Extract authoritative decision values from API v2/v1 note shapes."""
    values: list[str] = []
    if _is_decision_reply(note):
        values.extend(_decision_content_values(note.get("content") or {}))
    details = note.get("details") or {}
    replies: list[dict[str, Any]] = []
    if isinstance(details, dict):
        replies.extend(_reply_items(details.get("replies")))
    replies.extend(_reply_items(note.get("replies")))
    for reply in replies:
        if not _is_decision_reply(reply):
            continue
        values.extend(_decision_content_values(reply.get("content") or {}))
    return list(dict.fromkeys(values))


def _is_accept_decision(value: str) -> bool:
    # OpenReview's decision field is structured text.  Do not treat a track
    # label ("poster"/"oral") or prose such as "accepted by reviewer" as a
    # decision: only the finite decision vocabulary is authoritative.
    lowered = re.sub(r"\s+", " ", _text(value)).strip().casefold()
    if not lowered or _is_reject_decision(lowered):
        return False
    track = r"(?:poster|oral|spotlight)"
    return bool(re.fullmatch(rf"(?:accept|accepted)(?:\s*(?:\({track}\)|\[{track}\]|[-:]\s*{track}))?", lowered))


def _is_reject_decision(value: str) -> bool:
    lowered = value.casefold()
    return any(token in lowered for token in ("reject", "withdraw", "withdrawn", "desk reject", "decline", "not accept", "no accept"))


def _openreview_terminal_venue(venue_id: str) -> tuple[str, str] | None:
    """Return the registered base venue and terminal state for v1/v2 tabs.

    OpenReview moves withdrawn and desk-rejected submissions into explicit
    child venue IDs. Those records are not accepted papers, but silently
    filtering them hides a meaningful collection outcome from the source
    report. Keep the vocabulary finite and derive no status from free text.
    """
    normalized = unicodedata.normalize("NFKC", _text(venue_id)).strip().rstrip("/").casefold()
    match = re.fullmatch(
        r"(?P<base>.+/(?:conference))/(?P<state>withdrawn_submission|desk_rejected_submission|rejected_submission)",
        normalized,
    )
    if not match:
        return None
    return match.group("base"), match.group("state")


def _is_explicit_final_venue(venue_text: str, assigned_venue_id: str) -> bool:
    """Recognize only OpenReview's explicit final-venue labels.

    A free-form venue string such as ``"accepted"`` or ``"poster"`` is not
    evidence of acceptance.  The legacy API sometimes omits decision replies,
    but its final venue label includes a finite final track, such as
    ``PREFIX YEAR Conference (Poster)``. Require the venue id, year, prefix,
    and final track to agree before using that compatibility path.
    """
    # API serializers can use compatibility characters such as full-width
    # parentheses. Normalize those before applying the deliberately narrow
    # final-venue grammar; this is not a fuzzy acceptance match.
    venue = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", _text(venue_text))).strip().casefold()
    assigned = re.sub(r"\s+", "", unicodedata.normalize("NFKC", _text(assigned_venue_id))).strip().casefold()
    match = re.fullmatch(r"(?P<prefix>[a-z0-9-]+)(?:\.[a-z0-9-]+)?/(?P<year>(?:19|20)\d{2})/conference", assigned)
    if not venue or not match:
        return False
    prefix = re.escape(match.group("prefix").replace("-", " "))
    year = match.group("year")
    # Parenthesized and unparenthesized track labels occur in API v1/v2.  The
    # accepted vocabulary is deliberately finite; "submission" and other
    # free-form labels therefore cannot upgrade a record.
    return bool(re.fullmatch(
        rf"{prefix}\s+{year}\s+(?:conference\s*(?:\((?:poster|oral|spotlight)\)|(?:poster|oral|spotlight))|(?:poster|oral|spotlight))",
        venue,
    ))


def _registered_legacy_final_venue(venue_text: str) -> tuple[str, Any] | None:
    """Resolve a venue-less legacy final label only against registered IDs."""
    matches: list[tuple[str, Any]] = []
    for spec in VENUE_SPECS:
        for registered_id in spec.openreview_ids:
            if _is_explicit_final_venue(venue_text, registered_id):
                matches.append((registered_id, spec))
    return matches[0] if len(matches) == 1 else None


def _reply_forum(note: dict[str, Any]) -> str:
    for key in ("forum", "parentForum", "parentNote", "replyto"):
        value = note.get(key)
        if isinstance(value, dict):
            value = value.get("id") or value.get("forum")
        value = _text(value)
        if value:
            return value
    return ""


def _normalize_openreview_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_openreview_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_openreview_value(item) for item in value]
    if hasattr(value, "to_json") and callable(value.to_json):
        return _normalize_openreview_note(value)
    return value


def _normalize_openreview_note(note: Any) -> dict[str, Any]:
    """Convert v1/v2 Note objects to the parser's stable dictionary shape."""

    if isinstance(note, dict):
        return {str(key): _normalize_openreview_value(value) for key, value in note.items()}
    data: dict[str, Any] = {}
    raw_json = getattr(note, "to_json", None)
    if callable(raw_json):
        value = raw_json()
        if isinstance(value, dict):
            data.update({str(key): _normalize_openreview_value(item) for key, item in value.items()})
    # v2 Note.to_json intentionally omits details. Merge public attributes so
    # replies returned by ``details=replies`` are not discarded.
    for key, value in vars(note).items():
        if value is None:
            continue
        canonical_key = {
            "external_id": "externalId",
            "external_ids": "externalIds",
            "parent_invitations": "parentInvitations",
        }.get(key, key)
        data[canonical_key] = _normalize_openreview_value(value)
    return data


def _openreview_response(notes: list[dict[str, Any]], *, params: dict[str, str], endpoint: str) -> HttpResponse:
    query = parse.urlencode(params)
    url = f"{endpoint}?{query}" if query else endpoint
    body = json.dumps({"notes": notes}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return HttpResponse(url=url, status=200, headers={"content-type": "application/json"}, body=body)


class ArxivSource:
    name = "arxiv"

    def __init__(self, client: HttpClient):
        self.client = client

    def discover(self, plan: SearchPlan) -> list[PaperFacts]:
        return self.discover_result(plan).papers

    def discover_result(self, plan: SearchPlan) -> DiscoveryResult:
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        requests_attempted = 0
        requests_succeeded = 0
        records_scanned = 0
        records_filtered = 0
        for query in plan.queries:
            params = {
                "search_query": query,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": str(plan.max_results_per_query),
            }
            url = f"{ARXIV_API_URL}?{parse.urlencode(params)}"
            requests_attempted += 1
            try:
                response = self.client.get(
                    url,
                    min_interval=3.1,
                    max_bytes=20 * 1024 * 1024,
                    allowed_hosts={"export.arxiv.org"},
                )
                requests_succeeded += 1
                parsed, parsed_incomplete, stats = self.parse_feed_with_incomplete(response)
                papers.extend(parsed)
                incomplete.extend(parsed_incomplete)
                records_scanned += stats["scanned"]
                records_filtered += stats["filtered"]
            except Exception as exc:
                errors.append({
                    "query": query,
                    "stage": "atom_query",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:300],
                })
        report = {
            "source": self.name,
            "adapter": "atom",
            "status": "error" if errors and not papers else "partial" if errors or incomplete else "ok",
            "queries": list(plan.queries),
            "scanned": records_scanned,
            "fetched": requests_succeeded,
            "parsed": len(papers),
            "filtered": records_filtered,
            "truncated": False,
            "budget_exhausted": False,
            "incomplete": len(incomplete),
            "records_scanned": records_scanned,
            "records_valid": len(papers),
            "records_filtered": records_filtered,
            "records_incomplete": len(incomplete),
            "requests_attempted": requests_attempted,
            "requests_succeeded": requests_succeeded,
            "requests_failed": requests_attempted - requests_succeeded,
            "errors": errors,
        }
        return DiscoveryResult(papers, incomplete, [report])

    @staticmethod
    def parse_feed(response: HttpResponse) -> list[PaperFacts]:
        return ArxivSource.parse_feed_with_incomplete(response)[0]

    @staticmethod
    def parse_feed_with_incomplete(
        response: HttpResponse,
    ) -> tuple[list[PaperFacts], list[dict[str, Any]], dict[str, int]]:
        root = ET.fromstring(response.body)
        provenance = _provenance(response, source="arxiv_atom")
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        records_filtered = 0
        entries = root.findall("atom:entry", ATOM_NS)
        for index, entry in enumerate(entries):
            raw_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
            match = re.search(r"arxiv\.org/abs/([^?#]+)", raw_id)
            if not match:
                records_filtered += 1
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
            journal_ref = _text(entry.findtext("arxiv:journal_ref", default="", namespaces=ARXIV_NS)) or None
            landing_url = f"https://arxiv.org/abs/{arxiv_id}"
            source_metadata: dict[str, Any] = {
                "arxiv_id": arxiv_id,
                "versioned_id": versioned_id,
            }
            if journal_ref:
                # arXiv does not verify this string. Keep it for reconciliation
                # evidence only; it must never become a venue fact on its own.
                source_metadata["journal_ref"] = {"value": journal_ref, "verified": False}
                source_metadata["journal_ref_value"] = journal_ref
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
                identifiers={"arxiv": arxiv_id, **({"doi": doi} if doi else {})},
                alternate_links=({"arxiv_version": f"https://arxiv.org/abs/{versioned_id}"} if versioned_id != arxiv_id else {}),
                alternate_ids=[versioned_id] if versioned_id != arxiv_id else [],
                source_metadata=source_metadata,
                collection_tier="arxiv_fallback",
                match_state="unmatched",
                provenance={field: dict(provenance) for field in (
                    "title", "authors", "abstract", "published_at", "updated_at", "doi", "landing_url", "pdf_url"
                )},
            )
            try:
                paper.validate_discovered()
            except ValueError as exc:
                incomplete.append({
                    "source": ArxivSource.name,
                    "adapter": "atom",
                    "source_id": arxiv_id or f"entry-{index}",
                    "reason": "required_arxiv_field_missing",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:300],
                })
                continue
            papers.append(paper)
        return papers, incomplete, {
            "scanned": len(entries),
            "filtered": records_filtered,
        }

    def fetch_by_id(self, arxiv_id: str) -> PaperFacts:
        params = {"id_list": arxiv_id, "max_results": "2"}
        response = self.client.get(
            f"{ARXIV_API_URL}?{parse.urlencode(params)}",
            min_interval=3.1,
            max_bytes=20 * 1024 * 1024,
            allowed_hosts={"export.arxiv.org"},
        )
        matches = [paper for paper in self.parse_feed(response) if paper.source_id == arxiv_id]
        if len(matches) != 1:
            raise ValueError(f"arXiv identity lookup returned {len(matches)} matches for {arxiv_id}")
        return matches[0]


class OfficialSource:
    """Route registered formal venue groups to real official adapters."""

    name = "official"

    def __init__(self, client: HttpClient):
        self.client = client

    def discover(self, plan: SearchPlan) -> list[PaperFacts]:
        return self.discover_result(plan).papers

    def fetch_by_id(self, paper: PaperFacts) -> PaperFacts:
        spec_key, canonical_url, allowed_hosts = official_route_for_paper(paper)
        spec = get_venue_spec(spec_key)
        if spec is None or spec.adapter not in ADAPTERS:
            raise ValueError(f"no official venue adapter for {paper.paper_id}")
        response = self.client.get(
            canonical_url,
            min_interval=0.2,
            max_bytes=10 * 1024 * 1024,
            allowed_hosts=allowed_hosts,
        )
        source_id = paper.source_id
        parsed = parse_official_detail(
            response.text(),
            spec=spec,
            url=canonical_url,
            source_id=source_id,
            fallback_year=int(paper.published_at[:4]) if paper.published_at and paper.published_at[:4].isdigit() else None,
            response=response,
        )
        if parsed.paper is None or parsed.paper.paper_id != paper.paper_id or parsed.paper.source_id != source_id:
            raise ValueError(f"official identity lookup returned no exact match for {paper.paper_id}")
        return parsed.paper

    def discover_result(self, plan: SearchPlan) -> DiscoveryResult:
        specs = venue_specs_for_group(plan.venue_groups)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        reports: list[dict[str, Any]] = []
        routed = 0
        for spec in specs:
            adapter_type = ADAPTERS.get(spec.adapter or "")
            if adapter_type is None or "official" not in spec.source_kinds:
                continue
            routed += 1
            try:
                result = adapter_type(self.client).discover(plan, spec)
            except Exception as exc:
                reports.append({
                    "source": self.name,
                    "adapter": spec.adapter,
                    "venue_group": spec.key,
                    "status": "error",
                    "discovered": 0,
                    "scanned": 0,
                    "fetched": 0,
                    "filtered": 0,
                    "records_scanned": 0,
                    "records_valid": 0,
                    "records_filtered": 0,
                    "records_incomplete": 0,
                    "requests_attempted": 0,
                    "requests_succeeded": 0,
                    "requests_failed": 1,
                    "incomplete": 0,
                    "errors": [{
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:300],
                    }],
                })
                continue
            papers.extend(result.papers)
            incomplete.extend(result.incomplete)
            reports.extend(result.reports)
        if not routed:
            reports.append({
                "source": self.name,
                "adapter": None,
                "status": "skipped",
                "reason": "no registered official adapter for requested venue groups",
                "discovered": 0,
                "scanned": 0,
                "fetched": 0,
                "filtered": 0,
                "records_scanned": 0,
                "records_valid": 0,
                "records_filtered": 0,
                "records_incomplete": 0,
                "requests_attempted": 0,
                "requests_succeeded": 0,
                "requests_failed": 0,
                "incomplete": 0,
                "errors": [],
            })
        return DiscoveryResult(papers, incomplete, reports)


class OpenReviewSource:
    name = "openreview"

    def __init__(
        self,
        client_factory: OpenReviewClientFactory | Any | None = None,
        *,
        http_client: HttpClient | Any | None = None,
    ):
        self.client_factory = client_factory or OpenReviewClientFactory.from_env()
        # The optional client is injected by the pipeline so challenge
        # recovery uses the same bounded HTTP/headless broker as other
        # sources.  Do not construct a second client here: that would bypass
        # the process-wide fallback policy and make tests/network behavior
        # nondeterministic.
        self.http_client = http_client
        self.errors: list[dict[str, Any]] = []
        self._requests_attempted = 0
        self._requests_succeeded = 0
        self._requests_failed = 0
        self._last_client_version: str | None = None

    def discover(self, plan: SearchPlan) -> list[PaperFacts]:
        return self.discover_result(plan).papers

    def probe(self, venue_id: str) -> dict[str, Any]:
        """Run a bounded connectivity probe through the official client."""

        response = self._get_notes(
            {"content.venueid": venue_id, "limit": "1", "details": "replies"},
            stage="venue_query",
        )
        payload = response.json()
        notes = payload.get("notes", []) if isinstance(payload, dict) else []
        if not isinstance(notes, list) or not notes or not _text(notes[0].get("id")):
            raise ValueError("OpenReview venue query returned no paper")
        result = {
            "status": "ok",
            "http_status": response.status,
            "notes": len(notes),
            "client_version": self._last_client_version,
        }
        if self.errors:
            # v1 can legitimately serve an old venue after a v2 failure, but
            # a health check must still make the failed primary path visible.
            result["status"] = "partial"
            result["fallback_errors"] = [{
                key: error[key]
                for key in ("endpoint", "stage", "error_type", "http_status")
                if key in error
            } for error in self.errors]
        return result

    def discover_result(self, plan: SearchPlan) -> DiscoveryResult:
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        reports: list[dict[str, Any]] = []
        self.errors = []
        self._requests_attempted = 0
        self._requests_succeeded = 0
        self._requests_failed = 0
        page_size = min(plan.max_results_per_venue, 1000)
        for venue_id in plan.openreview_venues:
            offset = 0
            venue_papers = 0
            venue_scanned = 0
            venue_filtered = 0
            venue_incomplete: list[dict[str, Any]] = []
            venue_errors: list[dict[str, Any]] = []
            venue_truncated = False
            request_start = self._requests_attempted
            succeeded_start = self._requests_succeeded
            failed_start = self._requests_failed
            errors_start = len(self.errors)
            while offset < plan.max_results_per_venue:
                params = {
                    "content.venueid": venue_id,
                    "limit": str(min(page_size, plan.max_results_per_venue - offset)),
                    "offset": str(offset),
                    # ``replyCount`` is only a count. Decisions live in the
                    # reply payload and must be joined before classification.
                    "details": "replies",
                }
                try:
                    response = self._get_notes(params, stage="venue_query")
                    payload = response.json()
                    notes = payload.get("notes", []) if isinstance(payload, dict) else []
                    if not isinstance(notes, list):
                        raise ValueError("OpenReview notes response has non-list notes")
                    # Details may include replies.  Only root submissions are
                    # candidate records; counting replies as papers makes
                    # ``scanned`` and ``filtered`` meaningless.
                    root_notes = [note for note in notes if self._is_root_candidate(note)]
                    venue_scanned += len(root_notes)
                except Exception as exc:
                    error = {
                        "venue_id": venue_id,
                        "stage": openreview_failure_stage(exc, "venue_query"),
                        "error_type": type(exc).__name__,
                        "message": openreview_error_message(exc),
                    }
                    venue_errors.append(error)
                    break

                joined_notes = list(notes)
                for note in root_notes:
                    forum_id = _text(note.get("forum") or note.get("id"))
                    if not forum_id or _openreview_decisions(note):
                        continue
                    try:
                        joined_notes.extend(self._fetch_replies(forum_id))
                    except Exception as exc:
                        error_record = {
                            "source": self.name,
                            "adapter": self.name,
                            "venue_group": venue_id,
                            "source_id": forum_id,
                            "reason": "decision_replies_unavailable",
                            "missing": ["decision"],
                            "partial": {"error_type": type(exc).__name__, "message": openreview_error_message(exc)},
                        }
                        incomplete.append(error_record)
                        venue_incomplete.append(error_record)
                        venue_errors.append({
                            "venue_id": venue_id,
                            "forum_id": forum_id,
                            "stage": openreview_failure_stage(exc, "reply_query"),
                            "error_type": type(exc).__name__,
                            "message": openreview_error_message(exc),
                        })
                # A forum reply endpoint may include the root submission
                # again.  Deduplicate by note id before classification so a
                # single authoritative record cannot be emitted twice.
                deduplicated_notes: list[dict[str, Any]] = []
                seen_note_ids: set[str] = set()
                for joined_note in joined_notes:
                    if not isinstance(joined_note, dict):
                        continue
                    note_id = _text(joined_note.get("id"))
                    if note_id and note_id in seen_note_ids:
                        continue
                    if note_id:
                        seen_note_ids.add(note_id)
                    deduplicated_notes.append(joined_note)
                parsed, parsed_incomplete = self.parse_notes_with_incomplete(deduplicated_notes, venue_id=venue_id, response=response)
                papers.extend(parsed)
                incomplete.extend(parsed_incomplete)
                venue_papers += len(parsed)
                venue_incomplete.extend(parsed_incomplete)
                venue_filtered += max(0, len(root_notes) - len(parsed) - len(parsed_incomplete))
                if len(notes) < int(params["limit"]):
                    break
                offset += len(notes)
                if offset >= plan.max_results_per_venue:
                    venue_truncated = True
            report_errors = [*venue_errors, *self.errors[errors_start:]]
            reports.append({
                "source": self.name,
                "adapter": self.name,
                "venue_group": venue_id,
                "status": "error" if report_errors and not venue_papers else "partial" if report_errors or venue_incomplete else "ok",
                "requested_details": "replies",
                "scanned": venue_scanned,
                "fetched": self._requests_succeeded - succeeded_start,
                "discovered": venue_papers,
                "filtered": venue_filtered,
                "truncated": venue_truncated,
                "budget_exhausted": venue_truncated,
                "incomplete": len(venue_incomplete),
                "records_scanned": venue_scanned,
                "records_valid": venue_papers,
                "records_filtered": venue_filtered,
                "records_incomplete": len(venue_incomplete),
                "requests_attempted": self._requests_attempted - request_start,
                "requests_succeeded": self._requests_succeeded - succeeded_start,
                "requests_failed": self._requests_failed - failed_start,
                "errors": report_errors,
            })
        return DiscoveryResult(papers, incomplete, reports)

    def _get_notes(self, params: dict[str, str], *, stage: str = "venue_query") -> HttpResponse:
        """Use official API clients with explicit v2 -> v1 compatibility."""
        last_error: Exception | None = None
        for version, endpoint in (("v2", OPENREVIEW_API_URL), ("v1", OPENREVIEW_LEGACY_API_URL)):
            self._requests_attempted += 1
            try:
                client = self.client_factory.get(version)
                # The two official client generations expose different
                # venue filters: v2 accepts structured ``content`` while v1
                # requires the registered venue's ``/-/Submission``
                # invitation. Passing the v2 keyword to v1 raises TypeError
                # before any network request, making the compatibility path
                # appear to work while never reaching older deployments.
                client_params: dict[str, Any] = {}
                if "content.venueid" in params:
                    if version == "v2":
                        client_params["content"] = {"venueid": params["content.venueid"]}
                    else:
                        client_params["invitation"] = _v1_submission_invitation(params["content.venueid"])
                for key in ("id", "forum", "replyto", "details"):
                    if key in params:
                        client_params[key] = params[key]
                for key in ("limit", "offset"):
                    if key in params:
                        client_params[key] = int(params[key])
                raw_notes = client.get_notes(**client_params)
                if isinstance(raw_notes, tuple) and raw_notes and isinstance(raw_notes[0], list):
                    raw_notes = raw_notes[0]
                if not isinstance(raw_notes, list):
                    raise ValueError("OpenReview client returned non-list notes")
                notes = [_normalize_openreview_note(note) for note in raw_notes]
                # A successful but empty v2 response can be a compatibility
                # gap for older venue deployments. Probe v1 once at the first
                # page before accepting an empty result; later empty pages are
                # normal pagination termination and must not restart there.
                if not notes and version == "v2" and int(params.get("offset", 0)) == 0:
                    self._requests_succeeded += 1
                    self._last_client_version = version
                    continue
                # An empty first-page v1 response is not a valid discovery
                # result: it usually means the invitation route was wrong.
                # Surface it as an error rather than silently reporting zero
                # papers and losing the fallback failure in observability.
                if not notes and version == "v1" and stage == "venue_query" and int(params.get("offset", 0)) == 0:
                    raise ValueError(
                        "OpenReview v1 submission invitation returned no notes: "
                        f"{client_params.get('invitation', '')}"
                    )
                self._requests_succeeded += 1
                self._last_client_version = version
                return _openreview_response(notes, params=params, endpoint=endpoint)
            except Exception as exc:
                self._requests_failed += 1
                last_error = exc
                error_stage = openreview_failure_stage(exc, stage)
                self.errors.append({
                    "endpoint": version,
                    "stage": error_stage,
                    "params": dict(params),
                    "error_type": type(exc).__name__,
                    "http_status": getattr(exc, "status_code", getattr(exc, "code", None)),
                    "message": openreview_error_message(exc),
                })
                if version == "v2" and self._is_v2_challenge(exc):
                    # A supplied HTTP client is the only route to the
                    # deterministic api2 recovery.  Once that route is
                    # attempted, do not mask a challenge with the legacy v1
                    # client or silently reinterpret its response.
                    if self.http_client is not None:
                        recovered = self._recover_v2_notes(params, stage=stage)
                        if recovered is not None:
                            return recovered
                        break
                    # Keep the historical v1 compatibility behavior for
                    # callers that construct this source without an injected
                    # broker.  The production pipeline supplies one when
                    # headless recovery is enabled.
        if last_error is None:
            raise RuntimeError("OpenReview client request failed")
        raise last_error

    @staticmethod
    def _is_v2_challenge(exc: Exception) -> bool:
        """Return whether a v2 failure may use the bounded HTTP recovery."""

        # A 403 alone is not sufficient: the shared classifier deliberately
        # treats plain forbidden/auth responses as authentication failures.
        # Recovery is reserved for responses explicitly identified as an
        # anti-bot challenge.
        return openreview_failure_stage(exc, "venue_query") == "challenge"

    def _recover_v2_notes(self, params: dict[str, str], *, stage: str) -> HttpResponse | None:
        """Fetch and validate the fixed api2 response after a v2 challenge."""

        recovery_url = _openreview_recovery_url(params)
        self._requests_attempted += 1
        try:
            response = self.http_client.get(
                recovery_url,
                max_bytes=20 * 1024 * 1024,
                allowed_hosts=_OPENREVIEW_RECOVERY_HOSTS,
            )
            if not isinstance(response, HttpResponse):
                raise TypeError("OpenReview HTTP recovery returned an invalid response")
            if not 200 <= response.status < 300:
                raise HttpRequestError(response.status, response.url)
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("OpenReview recovery response must be an object")
            notes = payload.get("notes")
            if not isinstance(notes, list) or any(not isinstance(note, dict) for note in notes):
                raise ValueError("OpenReview recovery response has non-list notes")
            self._requests_succeeded += 1
            self._last_client_version = "v2_http"
            return response
        except Exception as exc:
            self._requests_failed += 1
            self.errors.append({
                "endpoint": "v2_http_recovery",
                "stage": openreview_failure_stage(exc, stage),
                "params": dict(params),
                "error_type": type(exc).__name__,
                "http_status": getattr(exc, "status_code", getattr(exc, "code", None)),
                "message": openreview_error_message(exc),
            })
            return None

    def _fetch_replies(self, forum_id: str) -> list[dict[str, Any]]:
        """Fetch at most three bounded reply pages for one forum."""
        replies: list[dict[str, Any]] = []
        limit = 100
        offset = 0
        for _page in range(3):
            response = self._get_notes({
                "forum": forum_id,
                "limit": str(limit),
                "offset": str(offset),
                "details": "replies",
            }, stage="reply_query")
            payload = response.json()
            notes = payload.get("notes", []) if isinstance(payload, dict) else []
            if not isinstance(notes, list):
                raise ValueError("OpenReview replies response has non-list notes")
            replies.extend(note for note in notes if isinstance(note, dict))
            if len(notes) < limit:
                break
            offset += len(notes)
        return replies

    @staticmethod
    def _is_root_note(note: dict[str, Any]) -> bool:
        content = note.get("content") or {}
        return bool(_text(content.get("title")) and _text(content.get("abstract"))) and not note.get("replyto") and not note.get("parentNote")

    @staticmethod
    def _is_root_candidate(note: dict[str, Any]) -> bool:
        content = note.get("content") or {}
        return bool(_text(content.get("title"))) and not note.get("replyto") and not note.get("parentNote")

    @staticmethod
    def parse_notes(notes: Iterable[dict[str, Any]], *, venue_id: str | None, response: HttpResponse) -> list[PaperFacts]:
        return OpenReviewSource.parse_notes_with_incomplete(notes, venue_id=venue_id, response=response)[0]

    @staticmethod
    def parse_notes_with_incomplete(
        notes: Iterable[dict[str, Any]], *, venue_id: str | None, response: HttpResponse
    ) -> tuple[list[PaperFacts], list[dict[str, Any]]]:
        notes = list(notes)
        reply_decisions: dict[str, list[str]] = {}
        for reply in notes:
            values = _openreview_decisions(reply)
            forum = _reply_forum(reply)
            if forum and values and (not _text((reply.get("content") or {}).get("title"))):
                reply_decisions.setdefault(forum, []).extend(values)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        seen_root_forums: set[str] = set()
        provenance = _provenance(response, source="openreview_api")
        for note in notes:
            content = note.get("content") or {}
            if not OpenReviewSource._is_root_candidate(note):
                continue
            root_forum = _text(note.get("forum") or note.get("id"))
            if root_forum and root_forum in seen_root_forums:
                continue
            if root_forum:
                seen_root_forums.add(root_forum)
            assigned_venue_id = _text(content.get("venueid"))
            venue_text = _text(content.get("venue"))
            requested_spec = get_venue_spec(venue_id)
            if not assigned_venue_id:
                # Older v1 submissions may omit venueid while the requested,
                # baseline-registered invitation still identifies their venue.
                # During direct identity refresh, accept only a final label
                # that exactly maps to one registry-owned OpenReview ID.
                legacy_assignment = _registered_legacy_final_venue(venue_text)
                if requested_spec is not None and venue_id:
                    if legacy_assignment is not None:
                        legacy_venue_id, _legacy_spec = legacy_assignment
                        normalized_legacy = unicodedata.normalize("NFKC", legacy_venue_id).strip().rstrip("/").casefold()
                        normalized_requested = unicodedata.normalize("NFKC", venue_id).strip().rstrip("/").casefold()
                        if normalized_legacy != normalized_requested:
                            continue
                    assigned_venue_id = venue_id
                elif legacy_assignment is not None:
                    assigned_venue_id, _legacy_spec = legacy_assignment
                else:
                    incomplete.append({
                        "source": "openreview",
                        "adapter": "openreview",
                        "venue_group": venue_id,
                        "source_id": _text(note.get("forum") or note.get("id")),
                        "reason": "missing_assigned_venue_id",
                    })
                    continue
            terminal_venue = _openreview_terminal_venue(assigned_venue_id)
            assigned_base_venue = terminal_venue[0] if terminal_venue else assigned_venue_id
            assigned_spec = get_venue_spec(assigned_base_venue)
            canonical_spec = assigned_spec or requested_spec
            if canonical_spec is None:
                incomplete.append({
                    "source": "openreview",
                    "adapter": "openreview",
                    "venue_group": venue_id,
                    "source_id": _text(note.get("forum") or note.get("id")),
                    "reason": "unregistered_assigned_venue",
                    "assigned_venue_id": assigned_venue_id,
                })
                continue
            # A plan venue is a concrete invitation, not a family selector.
            # A same-family result from another year must not leak into the
            # requested collection window. Direct refresh has no such filter.
            venue_matches = not venue_id or (
                unicodedata.normalize("NFKC", assigned_base_venue).strip().rstrip("/").casefold()
                == unicodedata.normalize("NFKC", venue_id).strip().rstrip("/").casefold()
            )
            if not venue_matches:
                continue
            forum_hint = _text(note.get("forum") or note.get("id"))
            if terminal_venue is not None:
                incomplete.append({
                    "source": "openreview",
                    "adapter": "openreview",
                    "venue_group": venue_id,
                    "source_id": forum_hint,
                    "reason": "rejected_or_withdrawn",
                    "terminal_venue_state": terminal_venue[1],
                })
                continue
            decisions = list(dict.fromkeys([*_openreview_decisions(note), *reply_decisions.get(forum_hint, [])]))
            accepted_by_reply = any(_is_accept_decision(value) for value in decisions)
            rejected_by_reply = any(_is_reject_decision(value) for value in decisions)
            accepted_by_venue = _is_explicit_final_venue(venue_text, assigned_venue_id)
            has_decision = bool(decisions)
            if rejected_by_reply or _is_reject_decision(venue_text):
                incomplete.append({"source": "openreview", "adapter": "openreview", "venue_group": venue_id, "source_id": forum_hint, "reason": "rejected_or_withdrawn", "decision_replies": decisions})
                continue
            # A positive reply is authoritative. The venue text fallback is
            # retained only for legacy fixtures that contain no reply payload.
            if not accepted_by_reply and not (accepted_by_venue and not has_decision):
                incomplete.append({"source": "openreview", "adapter": "openreview", "venue_group": venue_id, "source_id": forum_hint, "reason": "pending_decision", "decision_replies": decisions})
                continue
            forum_id = forum_hint
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
                # The raw OpenReview venue label is evidence only.  The fact
                # field must use the controlled registry name so a value such
                # as "Submission" cannot become the canonical venue.
                venue=canonical_spec.name,
                published_at=_iso_from_millis(note.get("pdate")),
                updated_at=_iso_from_millis(note.get("mdate")),
                doi=doi,
                landing_url=landing_url,
                pdf_url=f"https://openreview.net/pdf?id={parse.quote(forum_id)}",
                categories=[assigned_venue_id],
                platform_links=platform_links(title=title, landing_url=landing_url, doi=doi),
                identifiers={"openreview": forum_id, **({"doi": doi} if doi else {})},
                venue_evidence=[{
                    "source": "openreview",
                    "venue_id": assigned_venue_id or venue_id,
                    "venue": venue_text or None,
                    "decisions": decisions,
                    "verified": bool(accepted_by_reply or accepted_by_venue),
                }],
                source_metadata={
                    "note_id": _text(note.get("id")),
                    "forum_id": forum_id,
                    "decision_replies": decisions,
                },
                collection_tier="formal",
                match_state="canonical",
                provenance={field: dict(provenance) for field in (
                    "title", "authors", "abstract", "publication_status", "venue", "published_at", "doi", "landing_url", "pdf_url"
                )},
            )
            try:
                paper.validate_discovered()
            except ValueError:
                incomplete.append({"source": "openreview", "adapter": "openreview", "venue_group": venue_id, "source_id": forum_id, "reason": "required_field_missing", "decision_replies": decisions})
                continue
            papers.append(paper)
        return papers, incomplete

    def fetch_by_id(self, forum_id: str) -> PaperFacts:
        response = self._get_notes({"id": forum_id, "details": "replies"}, stage="identity_query")
        payload = response.json()
        notes = payload.get("notes", []) if isinstance(payload, dict) else []
        if isinstance(notes, list) and not any(_openreview_decisions(note) for note in notes if isinstance(note, dict)):
            notes.extend(self._fetch_replies(forum_id))
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
        return self.discover_result(plan).papers

    @staticmethod
    def expected_type(spec: Any) -> str:
        return "journal-article" if get_venue_spec(spec).key in {"tdsc", "tifs", "tops"} else "proceedings-article"

    def discover_result(self, plan: SearchPlan) -> DiscoveryResult:
        requested: list[str] = list(plan.crossref_venues)
        if plan.venue_groups:
            for group_spec in venue_specs_for_group(plan.venue_groups):
                if "crossref" in group_spec.source_kinds and group_spec.key not in requested:
                    requested.append(group_spec.key)
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        reports: list[dict[str, Any]] = []
        seen_specs: set[str] = set()
        for venue in requested:
            spec = get_venue_spec(venue)
            if spec is None or "crossref" not in spec.source_kinds:
                raise ValueError(f"Crossref venue is not in controlled registry: {venue}")
            if spec.key in seen_specs:
                continue
            seen_specs.add(spec.key)
            query_container = spec.crossref_container_titles[0] if spec.crossref_container_titles else spec.name
            expected_type = self.expected_type(spec)
            venue_errors: list[dict[str, Any]] = []
            venue_incomplete: list[dict[str, Any]] = []
            venue_papers = 0
            venue_scanned = 0
            venue_filtered = 0
            requests_attempted = 0
            requests_succeeded = 0
            for query in plan.queries:
                if venue_papers >= plan.max_results_per_venue:
                    break
                filters = [f"type:{expected_type}"]
                if plan.date_from:
                    filters.append(f"from-pub-date:{plan.date_from}")
                if plan.date_to:
                    filters.append(f"until-pub-date:{plan.date_to}")
                params = {
                    "query.bibliographic": discovery_query_for_general_index(query),
                    "query.container-title": query_container,
                    "filter": ",".join(filters),
                    "rows": str(min(plan.max_results_per_query, 1000, plan.max_results_per_venue - venue_papers)),
                    "select": "DOI,title,author,abstract,container-title,published,URL,link,subject,ISSN,type",
                }
                if self.contact_email:
                    params["mailto"] = self.contact_email
                requests_attempted += 1
                try:
                    response = self.client.get(
                        f"{CROSSREF_API_URL}?{parse.urlencode(params)}",
                        min_interval=0.1,
                        max_bytes=30 * 1024 * 1024,
                        allowed_hosts={"api.crossref.org"},
                    )
                    requests_succeeded += 1
                    parsed, parsed_incomplete, stats = self._parse_items_with_stats(response, expected_venue=spec)
                    papers.extend(parsed)
                    incomplete.extend(parsed_incomplete)
                    venue_papers += len(parsed)
                    venue_incomplete.extend(parsed_incomplete)
                    venue_scanned += stats["scanned"]
                    venue_filtered += stats["filtered"]
                except Exception as exc:
                    venue_errors.append({
                        "query": query,
                        "error_type": type(exc).__name__,
                        "http_status": getattr(exc, "code", None),
                        "message": str(exc)[:300],
                    })
            reports.append({
                "source": self.name,
                "adapter": self.name,
                "venue_group": spec.key,
                "status": "error" if venue_errors and not venue_papers else "partial" if venue_errors or venue_incomplete else "ok",
                "expected_crossref_type": expected_type,
                "scanned": venue_scanned,
                "fetched": requests_succeeded,
                "discovered": venue_papers,
                "filtered": venue_filtered,
                "truncated": venue_papers >= plan.max_results_per_venue,
                "budget_exhausted": venue_papers >= plan.max_results_per_venue,
                "incomplete": len(venue_incomplete),
                "records_scanned": venue_scanned,
                "records_valid": venue_papers,
                "records_filtered": venue_filtered,
                "records_incomplete": len(venue_incomplete),
                "requests_attempted": requests_attempted,
                "requests_succeeded": requests_succeeded,
                "requests_failed": requests_attempted - requests_succeeded,
                "queries": list(plan.queries),
                "errors": venue_errors,
            })
        if not requested:
            reports.append({
                "source": self.name,
                "adapter": self.name,
                "status": "skipped",
                "reason": "no registered Crossref venue groups requested",
                "discovered": 0,
                "scanned": 0,
                "fetched": 0,
                "filtered": 0,
                "records_scanned": 0,
                "records_valid": 0,
                "records_filtered": 0,
                "records_incomplete": 0,
                "requests_attempted": 0,
                "requests_succeeded": 0,
                "requests_failed": 0,
                "truncated": False,
                "budget_exhausted": False,
                "incomplete": 0,
                "errors": [],
            })
        return DiscoveryResult(papers, incomplete, reports)

    @staticmethod
    def parse_items(response: HttpResponse, *, expected_venue: str | Any) -> list[PaperFacts]:
        return CrossrefSource._parse_items_with_stats(response, expected_venue=expected_venue)[0]

    @staticmethod
    def parse_items_with_incomplete(
        response: HttpResponse, *, expected_venue: str | Any
    ) -> tuple[list[PaperFacts], list[dict[str, Any]]]:
        papers, incomplete, _stats = CrossrefSource._parse_items_with_stats(response, expected_venue=expected_venue)
        return papers, incomplete

    @staticmethod
    def _parse_items_with_stats(
        response: HttpResponse, *, expected_venue: str | Any
    ) -> tuple[list[PaperFacts], list[dict[str, Any]], dict[str, int]]:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Crossref response must be an object")
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ValueError("Crossref response message must be an object")
        if not isinstance(message.get("items"), list):
            raise ValueError("Crossref response message items must be an array")
        items = message["items"]
        provenance = _provenance(response, source="crossref_api")
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        records_filtered = 0
        spec = get_venue_spec(expected_venue)
        if spec is None:
            raise ValueError(f"Crossref venue is not in controlled registry: {expected_venue}")
        expected_type = CrossrefSource.expected_type(spec)
        for item in items:
            if not isinstance(item, dict):
                records_filtered += 1
                continue
            venues = _list(item.get("container-title"))
            raw_issns = item.get("ISSN") or []
            if isinstance(raw_issns, str):
                raw_issns = [raw_issns]
            issns = {str(value).strip() for value in raw_issns if str(value).strip()}
            if not any(spec.matches_container(value) for value in venues) and not (issns & set(spec.crossref_issns)):
                records_filtered += 1
                continue
            doi = normalize_doi(_text(item.get("DOI")))
            source_id = doi or _text(item.get("URL")) or f"item-{len(papers) + len(incomplete)}"
            actual_type = _text(item.get("type"))
            if actual_type != expected_type:
                incomplete.append({
                    "source": CrossrefSource.name,
                    "adapter": CrossrefSource.name,
                    "venue_group": spec.key,
                    "source_id": source_id,
                    "reason": "crossref_type_mismatch",
                    "missing": ["type"],
                    "partial": {"type": actual_type or None, "expected_type": expected_type, "container_titles": venues},
                })
                continue
            title = _text(item.get("title"))
            authors = []
            for author in item.get("author") or []:
                if not isinstance(author, dict):
                    continue
                name = " ".join(part for part in (_text(author.get("given")), _text(author.get("family"))) if part)
                if name:
                    authors.append(name)
            abstract = _strip_markup(_text(item.get("abstract")))
            pdf_url = ""
            pdf_hosts = _trusted_crossref_hosts(spec)
            for link in item.get("link") or []:
                candidate_url = str(link.get("URL", ""))
                candidate_host = parse.urlsplit(candidate_url).hostname
                if (
                    "pdf" in _text(link.get("content-type")).lower()
                    and candidate_url.startswith("https://")
                    and candidate_host
                    and candidate_host.casefold().rstrip(".") in pdf_hosts
                ):
                    pdf_url = candidate_url
                    break
            published_parts = ((item.get("published") or {}).get("date-parts") or [[]])[0]
            published_at = None
            if len(published_parts) >= 3:
                try:
                    year = int(published_parts[0])
                    month = int(published_parts[1])
                    day = int(published_parts[2])
                    published_at = datetime(year, month, day, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
                except (TypeError, ValueError, OverflowError):
                    published_at = None
            landing_url = f"https://doi.org/{doi}"
            missing = [
                field for field, value in (
                    ("doi", doi),
                    ("title", title),
                    ("authors", authors),
                    ("abstract", abstract),
                    ("published_at", published_at),
                    ("pdf_url", pdf_url),
                ) if not value
            ]
            if missing:
                incomplete.append({
                    "source": CrossrefSource.name,
                    "adapter": CrossrefSource.name,
                    "venue_group": spec.key,
                    "source_id": source_id,
                    "reason": "required_crossref_field_missing",
                    "missing": missing,
                    "partial": {"title": title, "authors": authors, "abstract": abstract, "doi": doi, "type": actual_type},
                })
                continue
            paper = PaperFacts(
                paper_id=f"doi:{doi}",
                source="crossref",
                source_id=doi,
                title=title,
                authors=authors,
                abstract=abstract,
                publication_status="published",
                # The controlled registry is the authority for the venue
                # label.  Crossref container-title is evidence used for
                # matching, not a free-form fact string that can vary across
                # deposits.
                venue=spec.name,
                published_at=published_at,
                updated_at=None,
                doi=doi,
                landing_url=landing_url,
                pdf_url=pdf_url,
                categories=_list(item.get("subject")),
                platform_links=platform_links(title=title, landing_url=landing_url, doi=doi),
                identifiers={"doi": doi},
                venue_evidence=[{
                    "source": "crossref",
                    "container_titles": venues,
                    "issn": sorted(issns),
                    "verified": bool(issns & set(spec.crossref_issns) or any(spec.matches_container(value) for value in venues)),
                }],
                source_metadata={"crossref_type": item.get("type") or "unknown", "issn": sorted(issns), "venue_group": spec.key},
                collection_tier="formal",
                match_state="canonical",
                provenance={field: dict(provenance) for field in (
                    "title", "authors", "abstract", "publication_status", "venue", "published_at", "doi", "landing_url", "pdf_url"
                )},
            )
            try:
                paper.validate_discovered()
            except ValueError as exc:
                incomplete.append({
                    "source": CrossrefSource.name,
                    "adapter": CrossrefSource.name,
                    "venue_group": spec.key,
                    "source_id": source_id,
                    "reason": "crossref_record_validation_failed",
                    "missing": [],
                    "partial": {"error_type": type(exc).__name__, "message": str(exc)[:300]},
                })
                continue
            papers.append(paper)
        return papers, incomplete, {
            "scanned": len(items),
            "filtered": records_filtered,
        }

    def fetch_by_doi(self, doi: str, *, expected_venue: str | Any | None = None) -> PaperFacts:
        normalized_doi = normalize_doi(doi)
        if not _DOI_RE.fullmatch(normalized_doi):
            raise ValueError("invalid DOI")
        response = self.client.get(
            f"{CROSSREF_API_URL}/{parse.quote(normalized_doi, safe='')}",
            min_interval=0.1,
            max_bytes=10 * 1024 * 1024,
            allowed_hosts={"api.crossref.org"},
        )
        payload = response.json()
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict):
            raise ValueError("Crossref identity lookup returned no work")
        wrapper = HttpResponse(response.url, response.status, response.headers, json.dumps({"message": {"items": [message]}}).encode())
        if expected_venue is not None:
            matches = self.parse_items(wrapper, expected_venue=expected_venue)
        else:
            matches = []
            for spec in VENUE_SPECS:
                if "crossref" not in spec.source_kinds:
                    continue
                matches.extend(self.parse_items(wrapper, expected_venue=spec))
            matches = list({paper.paper_id: paper for paper in matches}.values())
        if len(matches) != 1:
            raise ValueError(f"Crossref identity lookup returned {len(matches)} matches for {normalized_doi}")
        return matches[0]


class IeeeXploreSource:
    """Deterministic IEEE Xplore discovery using the official API.

    The API is optional because IEEE requires a subscription key.  Missing
    credentials and API failures are returned as source reports; callers can
    still use the separately requested Crossref adapter for the same registered
    venue.  This adapter never fabricates a record from a search snippet.
    """

    name = "ieee_xplore"

    def __init__(self, client: HttpClient, *, api_key: str | None = None):
        self.client = client
        self.api_key = api_key if api_key is not None else os.getenv(IEEE_XPLORE_API_KEY_ENV, "").strip()

    @staticmethod
    def _requested_specs(plan: SearchPlan) -> list[Any]:
        specs = venue_specs_for_group(plan.venue_groups)
        return [spec for spec in specs if spec.key in IEEE_XPLORE_VENUES and "ieee_xplore" in spec.source_kinds]

    @staticmethod
    def _query_url(*, spec: Any, query: str, api_key: str, max_records: int, start_record: int) -> tuple[str, str]:
        params = {
            "apikey": api_key,
            "format": "json",
            "max_records": str(max_records),
            "start_record": str(start_record),
            "publication_title": spec.name,
            "querytext": query,
        }
        request_url = f"{IEEE_XPLORE_API_URL}?{parse.urlencode(params)}"
        safe_params = {key: value for key, value in params.items() if key != "apikey"}
        provenance_url = f"{IEEE_XPLORE_API_URL}?{parse.urlencode(safe_params)}"
        return request_url, provenance_url

    @staticmethod
    def parse_articles(response: HttpResponse, *, spec: Any) -> tuple[list[PaperFacts], list[dict[str, Any]], dict[str, int]]:
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("articles"), list):
            raise ValueError("IEEE Xplore response articles must be an array")
        articles = payload["articles"]
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        filtered = 0
        provenance = _provenance(response, source="ieee_xplore_api")
        for index, item in enumerate(articles):
            if not isinstance(item, dict):
                filtered += 1
                continue
            publication = _text(item.get("publication_title") or item.get("publicationTitle"))
            if publication and not spec.matches_container(publication) and not spec.matches_name(publication):
                filtered += 1
                continue
            title = _text(item.get("title"))
            raw_authors = item.get("authors") or item.get("author") or []
            if isinstance(raw_authors, dict):
                raw_authors = raw_authors.get("authors") or raw_authors.get("author") or []
            authors: list[str] = []
            if isinstance(raw_authors, list):
                for author in raw_authors:
                    if isinstance(author, dict):
                        value = _text(author.get("full_name") or author.get("fullName") or author.get("name"))
                    else:
                        value = _text(author)
                    if value:
                        authors.append(value)
            abstract = _strip_markup(_text(item.get("abstract") or item.get("description")))
            doi = normalize_doi(_text(item.get("doi") or item.get("DOI")))
            article_number = _text(item.get("article_number") or item.get("articleNumber") or item.get("arnumber"))
            source_id = doi or article_number or f"record-{index}"
            landing_url = _text(item.get("html_url") or item.get("htmlUrl") or item.get("document_url") or item.get("documentUrl"))
            if not landing_url and article_number:
                landing_url = f"https://ieeexplore.ieee.org/document/{parse.quote(article_number, safe='')}"
            if not landing_url.startswith("https://ieeexplore.ieee.org/"):
                landing_url = ""
            pdf_url = _text(item.get("pdf_url") or item.get("pdfUrl"))
            pdf_host = parse.urlsplit(pdf_url).hostname if pdf_url else None
            if pdf_host and pdf_host.casefold().rstrip(".") != "ieeexplore.ieee.org":
                pdf_url = ""
            if pdf_url and not pdf_url.startswith("https://"):
                pdf_url = ""
            # IEEE Xplore's year field is not a publication timestamp.
            # Keep the date unknown unless the API supplies an exact date.
            published_at = None
            missing = [
                field for field, value in (
                    ("doi", doi), ("title", title), ("authors", authors),
                    ("abstract", abstract), ("landing_url", landing_url),
                    ("pdf_url", pdf_url),
                ) if not value
            ]
            if missing:
                incomplete.append({
                    "source": IeeeXploreSource.name,
                    "adapter": IeeeXploreSource.name,
                    "venue_group": spec.key,
                    "source_id": source_id,
                    "reason": "required_ieee_field_missing",
                    "missing": missing,
                    "partial": {"title": title, "authors": authors, "doi": doi, "publication_title": publication},
                })
                continue
            paper = PaperFacts(
                paper_id=f"doi:{doi}", source=IeeeXploreSource.name, source_id=doi,
                title=title, authors=authors, abstract=abstract,
                publication_status="published", venue=spec.name,
                published_at=published_at, updated_at=None, doi=doi,
                landing_url=landing_url, pdf_url=pdf_url,
                categories=[], platform_links=platform_links(title=title, landing_url=landing_url, doi=doi),
                identifiers={"doi": doi, **({"article_number": article_number} if article_number else {})},
                venue_evidence=[{"source": IeeeXploreSource.name, "publication_title": publication, "verified": True}],
                source_metadata={"venue_group": spec.key, "article_number": article_number, "api": "ieee_xplore"},
                collection_tier="formal", match_state="canonical",
                provenance={field: dict(provenance) for field in (
                    "title", "authors", "abstract", "publication_status", "venue", "published_at", "doi", "landing_url", "pdf_url"
                )},
            )
            try:
                paper.validate_discovered()
            except ValueError as exc:
                incomplete.append({
                    "source": IeeeXploreSource.name, "adapter": IeeeXploreSource.name,
                    "venue_group": spec.key, "source_id": source_id,
                    "reason": "ieee_record_validation_failed", "missing": [],
                    "partial": {"error_type": type(exc).__name__, "message": str(exc)[:300]},
                })
                continue
            papers.append(paper)
        return papers, incomplete, {"scanned": len(articles), "filtered": filtered}

    def discover(self, plan: SearchPlan) -> list[PaperFacts]:
        return self.discover_result(plan).papers

    def discover_result(self, plan: SearchPlan) -> DiscoveryResult:
        specs = self._requested_specs(plan)
        if not specs:
            return DiscoveryResult([], [], [{
                "source": self.name, "adapter": self.name, "status": "skipped",
                "reason": "no registered IEEE Xplore venue groups requested",
                "requests_attempted": 0, "requests_succeeded": 0, "requests_failed": 0,
                "records_scanned": 0, "records_valid": 0, "records_filtered": 0, "records_incomplete": 0,
            }])
        if not self.api_key:
            return DiscoveryResult([], [], [{
                "source": self.name, "adapter": self.name, "status": "error",
                "stage": "auth", "error_type": "missing_api_key",
                "error": f"{IEEE_XPLORE_API_KEY_ENV} is not configured",
                "requests_attempted": 0, "requests_succeeded": 0, "requests_failed": 0,
                "records_scanned": 0, "records_valid": 0, "records_filtered": 0, "records_incomplete": 0,
                "venues": [spec.key for spec in specs],
            }])
        papers: list[PaperFacts] = []
        incomplete: list[dict[str, Any]] = []
        reports: list[dict[str, Any]] = []
        for spec in specs:
            venue_papers: list[PaperFacts] = []
            venue_incomplete: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            requests_attempted = requests_succeeded = 0
            scanned = filtered = 0
            for query in plan.queries:
                if len(venue_papers) >= plan.max_results_per_venue:
                    break
                limit = min(plan.max_results_per_query, 100, plan.max_results_per_venue - len(venue_papers))
                request_url, provenance_url = self._query_url(
                    spec=spec,
                    query=discovery_query_for_general_index(query),
                    api_key=self.api_key,
                    max_records=limit,
                    start_record=1,
                )
                requests_attempted += 1
                try:
                    response = self.client.get(
                        request_url,
                        min_interval=0.25,
                        max_bytes=20 * 1024 * 1024,
                        provenance_url=provenance_url,
                        allowed_hosts={"ieeexploreapi.ieee.org"},
                    )
                    requests_succeeded += 1
                    parsed, parsed_incomplete, stats = self.parse_articles(response, spec=spec)
                    venue_papers.extend(parsed)
                    venue_incomplete.extend(parsed_incomplete)
                    scanned += stats["scanned"]
                    filtered += stats["filtered"]
                except Exception as exc:
                    code = getattr(exc, "code", None)
                    errors.append({"query": query, "error_type": type(exc).__name__, "http_status": code, "message": str(exc)[:300]})
            papers.extend(venue_papers)
            incomplete.extend(venue_incomplete)
            reports.append({
                "source": self.name, "adapter": self.name, "venue_group": spec.key,
                "status": "error" if errors and not venue_papers else "partial" if errors or venue_incomplete else "ok",
                "requests_attempted": requests_attempted, "requests_succeeded": requests_succeeded,
                "requests_failed": requests_attempted - requests_succeeded,
                "records_scanned": scanned, "records_valid": len(venue_papers),
                "records_filtered": filtered, "records_incomplete": len(venue_incomplete),
                "truncated": len(venue_papers) >= plan.max_results_per_venue,
                "errors": errors,
            })
        return DiscoveryResult(papers, incomplete, reports)

    def fetch_by_doi(self, doi: str, *, expected_venue: str | Any | None = None) -> PaperFacts:
        if not self.api_key:
            raise ValueError(f"{IEEE_XPLORE_API_KEY_ENV} is not configured")
        normalized_doi = normalize_doi(doi)
        if not _DOI_RE.fullmatch(normalized_doi):
            raise ValueError("invalid DOI")
        if expected_venue is not None:
            spec = get_venue_spec(expected_venue)
            specs = [spec] if spec is not None and spec.key in IEEE_XPLORE_VENUES else []
        else:
            specs = [item for item in VENUE_SPECS if item.key in IEEE_XPLORE_VENUES]
        if not specs:
            raise ValueError(f"IEEE Xplore venue is not in controlled registry: {expected_venue}")
        matches: list[PaperFacts] = []
        for spec in specs:
            request_url, provenance_url = self._query_url(spec=spec, query=normalized_doi, api_key=self.api_key, max_records=10, start_record=1)
            response = self.client.get(
                request_url,
                min_interval=0.25,
                max_bytes=20 * 1024 * 1024,
                provenance_url=provenance_url,
                allowed_hosts={"ieeexploreapi.ieee.org"},
            )
            papers, _incomplete, _stats = self.parse_articles(response, spec=spec)
            matches.extend(paper for paper in papers if normalize_doi(paper.doi or "") == normalized_doi)
        matches = list({paper.paper_id: paper for paper in matches}.values())
        if len(matches) != 1:
            raise ValueError(f"IEEE Xplore identity lookup returned {len(matches)} matches for {normalized_doi}")
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
            allowed_hosts={"serpapi.com"},
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
                "final_url": response.final_url,
                "transport": response.transport,
                "redirect_chain": list(response.redirect_chain),
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
            "final_url": response.final_url,
            "transport": response.transport,
            "redirect_chain": list(response.redirect_chain),
            "response_sha256": response.sha256,
        }
