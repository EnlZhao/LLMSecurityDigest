#!/usr/bin/env python3
"""Run one bounded authoritative refresh and BibTeX check for a venue."""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_security_digest.papers.models import PaperFacts, get_venue_spec, normalize_doi
from llm_security_digest.papers.pipeline import default_client, fetch_bibtex, refresh_authoritative
from llm_security_digest.papers.sources import CROSSREF_API_URL, CrossrefSource


def _key(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().rstrip("/").casefold()


def _venue_group(paper: PaperFacts) -> str:
    metadata = paper.source_metadata if isinstance(paper.source_metadata, dict) else {}
    value = metadata.get("venue_group")
    if isinstance(value, str) and value.strip():
        return value
    for evidence in paper.venue_evidence:
        if not isinstance(evidence, dict):
            continue
        value = evidence.get("venue_group")
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _openreview_venue_ids(paper: PaperFacts) -> set[str]:
    values = {_key(value) for value in paper.categories if isinstance(value, str)}
    for evidence in paper.venue_evidence:
        if isinstance(evidence, dict):
            value = evidence.get("venue_id")
            if isinstance(value, str):
                values.add(_key(value))
    return {value for value in values if value}


def _matches(paper: PaperFacts, *, source: str, venue: str, openreview_venue: str | None) -> bool:
    if source == "official":
        # Official discovery adapters use their concrete source name (for
        # example ``pmlr`` or ``cvf``), while the workflow uses the registry
        # venue group. The parser-owned group is the stable join key.
        return paper.source not in {"crossref", "openreview"} and _key(_venue_group(paper)) == _key(venue)
    if source == "crossref":
        return paper.source == "crossref" and _key(_venue_group(paper)) == _key(venue)
    if source == "openreview":
        expected = _key(openreview_venue)
        return paper.source == "openreview" and bool(expected) and expected in _openreview_venue_ids(paper)
    if source == "arxiv":
        return paper.source == "arxiv"
    raise ValueError(f"unsupported smoke source: {source!r}")


def _load_candidate(
    path: Path,
    *,
    source: str,
    venue: str,
    openreview_venue: str | None,
    sample_doi: str | None,
) -> PaperFacts:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise ValueError("candidate payload must contain a candidates array")
    candidates: list[PaperFacts] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        paper = PaperFacts.from_dict(value)
        if _matches(paper, source=source, venue=venue, openreview_venue=openreview_venue):
            candidates.append(paper)
    if sample_doi and source == "crossref":
        wanted = normalize_doi(sample_doi)
        for paper in candidates:
            if normalize_doi(paper.doi or "") == wanted:
                return paper
    if candidates:
        return candidates[0]
    selector = openreview_venue if source == "openreview" else venue
    raise ValueError(f"no candidate matched {source}/{selector}")


def _crossref_smoke_identity(*, client, doi: str, venue: str) -> SimpleNamespace:
    """Build an ephemeral identity from Crossref for BibTeX-only smoke checks.

    Crossref deposits often omit an abstract or an openly downloadable PDF.
    Such a work must stay out of ``facts.json``, but its DOI content
    negotiation endpoint can still be tested against the authoritative title,
    author list, and registered container. This object is never materialized.
    """
    normalized_doi = normalize_doi(doi)
    spec = get_venue_spec(venue)
    if spec is None or "crossref" not in spec.source_kinds:
        raise ValueError(f"Crossref smoke venue is not registered: {venue}")
    response = client.get(
        f"{CROSSREF_API_URL}/{quote(normalized_doi, safe='')}",
        max_bytes=10 * 1024 * 1024,
        allowed_hosts={"api.crossref.org"},
    )
    payload = response.json()
    item = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(item, dict):
        raise ValueError("Crossref DOI smoke response has no message")
    expected_type = CrossrefSource.expected_type(spec)
    if str(item.get("type") or "").strip() != expected_type:
        raise ValueError("Crossref DOI smoke record has the wrong publication type")
    containers = item.get("container-title") or []
    if isinstance(containers, str):
        containers = [containers]
    raw_issns = item.get("ISSN") or []
    if isinstance(raw_issns, str):
        raw_issns = [raw_issns]
    if not (
        any(spec.matches_container(str(value)) for value in containers)
        or bool(set(str(value).strip() for value in raw_issns) & set(spec.crossref_issns))
    ):
        raise ValueError("Crossref DOI smoke record is outside the registered venue")
    titles = item.get("title") or []
    title = str(titles[0]).strip() if titles and isinstance(titles[0], str) else ""
    authors = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        value = " ".join(
            part.strip()
            for part in (str(author.get("given") or ""), str(author.get("family") or ""))
            if part.strip()
        )
        if value:
            authors.append(value)
    if not title or not authors:
        raise ValueError("Crossref DOI smoke record has no title or authors")
    return SimpleNamespace(
        paper_id=f"doi:{normalized_doi}",
        source="crossref",
        source_id=normalized_doi,
        doi=normalized_doi,
        title=title,
        authors=authors,
        source_metadata={},
        landing_url=f"https://doi.org/{normalized_doi}",
        provenance={"title": {"source_url": response.url, "response_sha256": response.sha256}},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--source", choices=("official", "crossref", "openreview", "arxiv"), required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--openreview-venue")
    parser.add_argument("--sample-doi", help="authoritative Crossref DOI used only when discovery returned an incomplete record")
    args = parser.parse_args()

    client = default_client()
    sample_identity = False
    try:
        paper = _load_candidate(
            args.candidates,
            source=args.source,
            venue=args.venue,
            openreview_venue=args.openreview_venue,
            sample_doi=args.sample_doi,
        )
    except ValueError:
        if args.source != "crossref" or not args.sample_doi:
            raise
        paper = _crossref_smoke_identity(client=client, doi=args.sample_doi, venue=args.venue)
        sample_identity = True
    else:
        paper.validate_discovered()
    if sample_identity:
        # The sample route intentionally covers deposits that lack the
        # abstract/PDF required for facts.json. Its title/authors/DOI came
        # directly from Crossref above, so only BibTeX is exercised here.
        refreshed = paper
    else:
        refreshed = refresh_authoritative(paper, client=client)
        refreshed.validate_discovered()
    bibtex, bibtex_url, provenance = fetch_bibtex(refreshed, client=client)
    if not bibtex.strip() or not bibtex_url.startswith("https://"):
        raise ValueError("authoritative BibTeX response was empty or non-HTTPS")
    print(json.dumps({
        "status": "ok",
        "source": args.source,
        "venue": args.venue,
        "paper_id": refreshed.paper_id,
        "bibtex_url": bibtex_url,
        "bibtex_endpoint_kind": provenance.get("endpoint_kind", provenance.get("source")),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
