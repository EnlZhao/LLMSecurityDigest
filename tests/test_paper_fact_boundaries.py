import json
from urllib.parse import parse_qs, urlparse

import pytest

from llm_security_digest.papers.http import HttpRequestError, HttpResponse
from llm_security_digest.papers.http import _safe_url
from llm_security_digest.papers.headless import HeadlessDiscoveryError, _normalized_allowed_hosts
from llm_security_digest.papers.models import PaperFacts, SearchPlan, SelectionEntry, get_venue_spec
from llm_security_digest.papers.openreview_client import openreview_error_message, openreview_failure_stage
from llm_security_digest.papers import pipeline
from llm_security_digest.papers.official import _pdf_url, _tree
from llm_security_digest.papers.sources import (
    ArxivSource,
    CrossrefSource,
    IeeeXploreSource,
    OpenReviewSource,
    _is_explicit_final_venue,
    discovery_query_for_general_index,
    reconcile_arxiv_to_formal,
    trusted_fulltext_url,
)


def _paper(*, paper_id: str, source: str, source_id: str, title: str, authors: list[str], doi: str | None = None) -> PaperFacts:
    return PaperFacts(
        paper_id=paper_id,
        source=source,
        source_id=source_id,
        title=title,
        authors=authors,
        abstract="Authoritative abstract.",
        publication_status="published" if source != "arxiv" else "preprint",
        venue="Registered Venue" if source != "arxiv" else None,
        published_at="2026-01-02T00:00:00Z",
        updated_at=None,
        doi=doi,
        landing_url="https://example.org/paper",
        pdf_url="https://example.org/paper.pdf",
        collection_tier="formal" if source != "arxiv" else "arxiv_fallback",
        match_state="canonical" if source != "arxiv" else "unmatched",
    )


def _pmlr_paper() -> PaperFacts:
    return _paper(
        paper_id="pmlr:abad-rocamora24a:v235",
        source="pmlr",
        source_id="abad-rocamora24a:v235",
        title="PMLR paper",
        authors=["Alice Example"],
    )


def test_keyword_matching_normalizes_unicode_without_changing_fact_fields() -> None:
    paper = _paper(
        paper_id="crossref:unicode", source="crossref", source_id="10.1234/unicode",
        title="Ｆｕｌｌｗｉｄｔｈ Caf\u00e9", authors=["Alice Example"], doi="10.1234/unicode",
    )

    assert pipeline._matches_keywords(paper, ["fullwidth cafe\u0301"])
    assert not pipeline._matches_keywords(paper, ["different topic"])


def test_collect_uses_a_bounded_discovery_buffer_before_keyword_filtering(monkeypatch) -> None:
    papers = [
        _paper(paper_id="arxiv:noise-1", source="arxiv", source_id="noise-1", title="Noise one", authors=["Alice Example"]),
        _paper(paper_id="arxiv:noise-2", source="arxiv", source_id="noise-2", title="Noise two", authors=["Alice Example"]),
        _paper(paper_id="arxiv:target", source="arxiv", source_id="target", title="Target paper", authors=["Alice Example"]),
    ]
    seen_limits: list[tuple[int, int]] = []

    class _Arxiv:
        def __init__(self, *_args, **_kwargs):
            pass

        def discover_result(self, plan):
            seen_limits.append((plan.max_results_per_query, plan.max_results_per_venue))
            return type("Result", (), {"papers": papers[:plan.max_results_per_venue], "incomplete": [], "reports": []})()

    monkeypatch.setattr(pipeline, "ArxivSource", _Arxiv)
    payload = pipeline.collect(SearchPlan(
        queries=["security"], sources=["arxiv"], filter_keywords=["target"],
        max_results_per_query=2, max_results_per_venue=2, target=1,
    ))

    assert seen_limits == [(3, 3)]
    assert [paper["paper_id"] for paper in payload["candidates"]] == ["arxiv:target"]
    assert payload["discovery_limits"] == {
        "requested_max_results_per_query": 2,
        "requested_max_results_per_venue": 2,
        "used_max_results_per_query": 3,
        "used_max_results_per_venue": 3,
        "filtering_buffer": True,
    }


def test_discovery_plan_does_not_claim_a_buffer_after_hard_caps() -> None:
    plan = SearchPlan(
        queries=["security"], filter_keywords=["target"], target=10,
        max_results_per_query=1000, max_results_per_venue=5000,
    )

    assert pipeline._discovery_plan(plan) is plan


def test_selection_entries_require_the_declared_bounded_schema(tmp_path) -> None:
    path = tmp_path / "selection.json"
    base = {
        "paper_id": "crossref:one",
        "score": 1.0,
        "category": "Security",
        "reason": "ranked",
        "track": "core",
    }

    path.write_text(json.dumps({"selections": [base]}), encoding="utf-8")
    assert SelectionEntry.load_many(path)[0].paper_id == "crossref:one"

    for field in base:
        missing = dict(base)
        del missing[field]
        path.write_text(json.dumps({"selections": [missing]}), encoding="utf-8")
        with pytest.raises(ValueError, match="missing required fields"):
            SelectionEntry.load_many(path)


@pytest.mark.parametrize("score", [True, "1", float("nan"), float("inf"), float("-inf")])
def test_selection_scores_must_be_finite_numbers(tmp_path, score) -> None:
    path = tmp_path / "selection.json"
    value = {
        "paper_id": "crossref:one",
        "score": score,
        "category": "Security",
        "reason": "ranked",
        "track": "core",
    }

    path.write_text(json.dumps({"selections": [value]}), encoding="utf-8")
    with pytest.raises(ValueError, match="finite number"):
        SelectionEntry.load_many(path)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("paper_id", 1),
        ("category", 1),
        ("reason", 1),
        ("track", 1),
        ("paper_id", "x" * 321),
        ("category", "x" * 161),
        ("reason", "x" * 2001),
    ),
)
def test_selection_string_fields_are_typed_and_bounded(tmp_path, field, value) -> None:
    path = tmp_path / "selection.json"
    selection = {
        "paper_id": "crossref:one",
        "score": 1.0,
        "category": "Security",
        "reason": "ranked",
        "track": "core",
    }
    selection[field] = value

    path.write_text(json.dumps({"selections": [selection]}), encoding="utf-8")
    with pytest.raises(ValueError):
        SelectionEntry.load_many(path)


def test_selection_array_is_bounded(tmp_path) -> None:
    path = tmp_path / "selection.json"
    selections = [
        {
            "paper_id": f"crossref:{index}",
            "score": 1.0,
            "category": "Security",
            "reason": "ranked",
            "track": "core",
        }
        for index in range(101)
    ]

    path.write_text(json.dumps({"selections": selections}), encoding="utf-8")
    with pytest.raises(ValueError, match="more than 100 entries"):
        SelectionEntry.load_many(path)


def test_openreview_failure_message_redacts_challenge_urls() -> None:
    message = openreview_error_message(Exception(
        "Challenge verification required (2026-08-04-709430): https://openreview.net/challenge?redirect=https%3A%2F%2Fapi2.openreview.net%2Fnotes, 'reqId': 'transient-123'"
    ))

    assert "Challenge verification required" in message
    assert "openreview.net" not in message
    assert "api2.openreview.net" not in message
    assert "transient-123" not in message
    assert "2026-08-04-709430" not in message
    assert "<redacted-url>" in message
    assert "Challenge verification required (<redacted>)" in message
    assert "'reqId': '<redacted>'" in message


def test_optional_ieee_xplore_runs_only_after_no_key_sources_and_arxiv() -> None:
    sources = ["ieee_xplore", "arxiv", "crossref", "openreview", "official"]

    assert sorted(sources, key=lambda source: pipeline._COLLECTION_SOURCE_PRIORITY.get(source, 4)) == [
        "official", "crossref", "openreview", "arxiv", "ieee_xplore",
    ]


def test_pmlr_raw_pdf_path_requires_matching_volume() -> None:
    paper = _pmlr_paper()

    assert trusted_fulltext_url(
        paper,
        "https://raw.githubusercontent.com/mlresearch/v235/main/assets/abad-rocamora24a/abad-rocamora24a.pdf",
    )


def test_pmlr_raw_pdf_path_rejects_wrong_volume() -> None:
    paper = _pmlr_paper()

    assert not trusted_fulltext_url(
        paper,
        "https://raw.githubusercontent.com/mlresearch/v236/main/assets/abad-rocamora24a/abad-rocamora24a.pdf",
    )


class _ResponseClient:
    def __init__(self, response: HttpResponse):
        self.response = response

    def get(self, *_args, **_kwargs) -> HttpResponse:
        return self.response


class _ErrorClient:
    def __init__(self, error: Exception):
        self.error = error

    def get(self, *_args, **_kwargs):
        raise self.error


def _crossref_response(item: dict) -> HttpResponse:
    return HttpResponse(
        url="https://api.crossref.org/works",
        final_url="https://api.crossref.org/works",
        status=200,
        headers={},
        body=json.dumps({"message": {"items": [item]}}).encode(),
    )


def _crossref_item() -> dict:
    return {
        "DOI": "10.1109/SP.2026.1234567",
        "title": ["Crossref authoritative title"],
        "author": [{"given": "Alice", "family": "Example"}],
        "abstract": "Crossref authoritative abstract.",
        "container-title": ["IEEE Symposium on Security and Privacy"],
        "published": {"date-parts": [[2026, 1, 2]]},
        "link": [{"URL": "https://ieeexplore.ieee.org/document/1234567.pdf", "content-type": "application/pdf"}],
        "ISSN": ["1081-6011"],
        "type": "proceedings-article",
    }


def _ieee_response(article: dict) -> HttpResponse:
    return HttpResponse(
        url="https://ieeexploreapi.ieee.org/api/v1/search/articles",
        final_url="https://ieeexploreapi.ieee.org/api/v1/search/articles",
        status=200,
        headers={},
        body=json.dumps({"articles": [article]}).encode(),
    )


def _ieee_article() -> dict:
    return {
        "doi": "10.1109/SP.2026.1234567",
        "title": "IEEE authoritative title",
        "authors": [{"full_name": "Alice Example"}],
        "abstract": "IEEE authoritative abstract.",
        "publication_title": "IEEE Symposium on Security and Privacy",
        "article_number": "1234567",
        "html_url": "https://ieeexplore.ieee.org/document/1234567",
        "pdf_url": "https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=1234567",
    }


def test_arxiv_journal_reference_remains_unmatched_preprint() -> None:
    response = HttpResponse(
        url="https://export.arxiv.org/api/query",
        final_url="https://export.arxiv.org/api/query",
        status=200,
        headers={},
        body=b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
          <entry><id>https://arxiv.org/abs/2601.12345v2</id><title>Exact Paper</title>
          <summary>Original abstract.</summary><author><name>Alice Example</name></author>
          <published>2026-01-02T00:00:00Z</published><updated>2026-01-03T00:00:00Z</updated>
          <arxiv:journal_ref>Imaginary Conference 2026</arxiv:journal_ref></entry></feed>''',
    )

    papers = ArxivSource.parse_feed(response)

    assert len(papers) == 1
    assert papers[0].publication_status == "preprint"
    assert papers[0].venue is None
    assert papers[0].match_state == "unmatched"
    assert papers[0].source_metadata["journal_ref"]["verified"] is False


def test_arxiv_discovery_respects_per_venue_budget() -> None:
    def response(arxiv_id: str) -> HttpResponse:
        return HttpResponse(
            url="https://export.arxiv.org/api/query",
            final_url="https://export.arxiv.org/api/query",
            status=200,
            headers={},
            body=(
                '<feed xmlns="http://www.w3.org/2005/Atom">'
                f"<entry><id>https://arxiv.org/abs/{arxiv_id}</id>"
                f"<title>Paper {arxiv_id}</title><summary>Abstract {arxiv_id}</summary>"
                "<author><name>Alice Example</name></author>"
                "<published>2026-01-02T00:00:00Z</published>"
                "<updated>2026-01-03T00:00:00Z</updated></entry></feed>"
            ).encode(),
        )

    class Client:
        def __init__(self) -> None:
            self.responses = [response("2601.12345"), response("2601.12346")]
            self.calls: list[str] = []

        def get(self, url: str, **_kwargs) -> HttpResponse:
            self.calls.append(url)
            return self.responses.pop(0)

    client = Client()
    result = ArxivSource(client).discover_result(SearchPlan(
        queries=["ti:first", "ti:second"],
        sources=["arxiv"],
        max_results_per_query=50,
        max_results_per_venue=1,
        openreview_venues=[],
        crossref_venues=[],
    ))

    assert len(result.papers) == 1
    assert len(client.calls) == 1
    assert "max_results=1" in client.calls[0]
    assert result.reports[0]["requests_attempted"] == 1
    assert result.reports[0]["truncated"] is True
    assert result.reports[0]["budget_exhausted"] is True


def test_arxiv_deduplicates_cross_query_hits_before_budget() -> None:
    def response(arxiv_ids: list[str]) -> HttpResponse:
        entries = "".join(
            f"<entry><id>https://arxiv.org/abs/{arxiv_id}</id>"
            f"<title>Paper {arxiv_id}</title><summary>Abstract {arxiv_id}</summary>"
            "<author><name>Alice Example</name></author>"
            "<published>2026-01-02T00:00:00Z</published>"
            "<updated>2026-01-03T00:00:00Z</updated></entry>"
            for arxiv_id in arxiv_ids
        )
        return HttpResponse(
            url="https://export.arxiv.org/api/query",
            final_url="https://export.arxiv.org/api/query",
            status=200,
            headers={},
            body=(f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>').encode(),
        )

    class Client:
        def __init__(self) -> None:
            self.responses = [response(["2601.12345"]), response(["2601.12345", "2601.12346"])]
            self.calls: list[str] = []

        def get(self, url: str, **_kwargs) -> HttpResponse:
            self.calls.append(url)
            return self.responses.pop(0)

    client = Client()
    result = ArxivSource(client).discover_result(SearchPlan(
        queries=["ti:first", "ti:second"],
        sources=["arxiv"],
        max_results_per_query=50,
        max_results_per_venue=2,
        openreview_venues=[],
        crossref_venues=[],
    ))

    assert [paper.source_id for paper in result.papers] == ["2601.12345", "2601.12346"]
    assert len(client.calls) == 2
    assert result.reports[0]["records_filtered"] == 1


def test_arxiv_needs_exact_title_first_author_and_author_similarity_without_doi() -> None:
    arxiv = _paper(
        paper_id="arxiv:2601.12345", source="arxiv", source_id="2601.12345",
        title="Unicode-Safe Paper", authors=["Alice Example", "Bob Example"],
    )
    formal = _paper(
        paper_id="official:paper", source="official", source_id="paper",
        title="Unicode-Safe Paper", authors=["Alice Example", "Carol Example"],
    )

    canonical, evidence = reconcile_arxiv_to_formal(arxiv, [formal])

    assert canonical is None
    assert evidence["state"] == "unmatched"


def test_openreview_challenge_is_not_misreported_as_login_failure() -> None:
    class ChallengeError(Exception):
        code = 403

    class AuthError(Exception):
        code = 401

    assert openreview_failure_stage(ChallengeError("Challenge verification required"), "venue_query") == "challenge"
    assert openreview_failure_stage(AuthError("authentication challenge required"), "venue_query") == "auth"


def test_openreview_v2_acceptance_requires_an_explicit_decision_reply() -> None:
    response = HttpResponse(
        url="https://api2.openreview.net/notes",
        final_url="https://api2.openreview.net/notes",
        status=200,
        headers={},
        body=b"{}",
    )
    submission = {
        "id": "submission-id",
        "forum": "forum-id",
        "content": {
            "title": "Verified OpenReview Paper",
            "authors": ["Alice Example", "Bob Example"],
            "abstract": "Authoritative abstract from the OpenReview submission.",
            "venueid": "ICLR.cc/2025/Conference",
            "venue": "ICLR 2025 Conference",
        },
    }
    accepted = {
        "id": "decision-id",
        "forum": "forum-id",
        "invitations": ["ICLR.cc/2025/Conference/-/Decision"],
        "content": {"decision": "Accept (Poster)"},
    }
    rejected = {
        "id": "rejected-id",
        "forum": "other-forum",
        "invitations": ["ICLR.cc/2025/Conference/-/Decision"],
        "content": {"decision": "Reject"},
    }
    pending = {
        "id": "pending-id",
        "forum": "pending-forum",
        "content": {
            "title": "Pending Paper",
            "authors": ["Alice Example"],
            "abstract": "Pending abstract.",
            "venueid": "ICLR.cc/2025/Conference",
            "venue": "ICLR 2025 Conference",
        },
    }

    papers, incomplete = OpenReviewSource.parse_notes_with_incomplete(
        [submission, accepted, rejected, pending],
        venue_id="ICLR.cc/2025/Conference",
        response=response,
    )

    assert [paper.paper_id for paper in papers] == ["openreview:forum-id"]
    assert papers[0].publication_status == "accepted"
    assert papers[0].venue == "International Conference on Learning Representations"
    assert {item["reason"] for item in incomplete} == {"pending_decision"}


def test_openreview_rejected_and_withdrawn_submissions_never_become_facts() -> None:
    response = HttpResponse(
        url="https://api2.openreview.net/notes",
        final_url="https://api2.openreview.net/notes",
        status=200,
        headers={},
        body=b"{}",
    )
    rejected = {
        "id": "rejected-submission",
        "forum": "rejected-forum",
        "content": {
            "title": "Rejected Paper",
            "authors": ["Alice Example"],
            "abstract": "Rejected abstract.",
            "venueid": "ICLR.cc/2025/Conference",
            "venue": "ICLR 2025 Conference",
        },
    }
    rejection = {
        "id": "rejected-decision",
        "forum": "rejected-forum",
        "invitations": ["ICLR.cc/2025/Conference/-/Decision"],
        "content": {"decision": "Reject"},
    }
    withdrawn = {
        "id": "withdrawn-submission",
        "forum": "withdrawn-forum",
        "content": {
            "title": "Withdrawn Paper",
            "authors": ["Bob Example"],
            "abstract": "Withdrawn abstract.",
            "venueid": "ICLR.cc/2025/Conference",
            "venue": "Withdrawn",
        },
    }

    papers, incomplete = OpenReviewSource.parse_notes_with_incomplete(
        [rejected, rejection, withdrawn],
        venue_id="ICLR.cc/2025/Conference",
        response=response,
    )

    assert papers == []
    assert [item["reason"] for item in incomplete] == ["rejected_or_withdrawn", "rejected_or_withdrawn"]


def test_openreview_legacy_final_venue_is_unicode_normalized_but_not_generic() -> None:
    venue_id = "ICLR.cc/2025/Conference"

    assert _is_explicit_final_venue("iclr 2025 conference (poster)", venue_id)
    assert _is_explicit_final_venue("ICLR 2025 Conference（Poster）", venue_id)
    assert not _is_explicit_final_venue("ICLR 2025 Conference", venue_id)


def test_openreview_terminal_venues_are_visible_and_v1_uses_registered_request_venue() -> None:
    response = HttpResponse(
        url="https://api.openreview.net/notes",
        final_url="https://api.openreview.net/notes",
        status=200,
        headers={},
        body=b"{}",
    )
    legacy_accepted = {
        "id": "legacy-submission",
        "forum": "legacy-forum",
        "content": {
            "title": "Legacy Accepted Paper",
            "authors": [{"fullname": "Alice Example", "username": "alice"}],
            "abstract": "Legacy authoritative abstract.",
            "venue": "ICLR 2025 Conference (Poster)",
        },
    }
    withdrawn = {
        "id": "withdrawn-submission",
        "forum": "withdrawn-forum",
        "content": {
            "title": "Withdrawn Paper",
            "authors": ["Bob Example"],
            "abstract": "Withdrawn abstract.",
            "venueid": "ICLR.cc/2025/Conference/Withdrawn_Submission",
        },
    }

    papers, incomplete = OpenReviewSource.parse_notes_with_incomplete(
        [legacy_accepted, withdrawn],
        venue_id="ICLR.cc/2025/Conference",
        response=response,
    )

    assert [paper.paper_id for paper in papers] == ["openreview:legacy-forum"]
    assert papers[0].authors == ["Alice Example"]
    assert incomplete == [{
        "source": "openreview",
        "adapter": "openreview",
        "venue_group": "ICLR.cc/2025/Conference",
        "source_id": "withdrawn-forum",
        "reason": "rejected_or_withdrawn",
        "terminal_venue_state": "withdrawn_submission",
    }]


def test_openreview_rejects_cross_year_venue_and_refreshes_registered_legacy_final_note() -> None:
    response = HttpResponse(
        url="https://api2.openreview.net/notes",
        final_url="https://api2.openreview.net/notes",
        status=200,
        headers={},
        body=b"{}",
    )
    cross_year = {
        "id": "cross-year-submission",
        "forum": "cross-year-forum",
        "content": {
            "title": "Cross Year Paper",
            "authors": ["Alice Example"],
            "abstract": "Cross year abstract.",
            "venueid": "ICLR.cc/2024/Conference",
        },
    }
    decision = {
        "id": "cross-year-decision",
        "forum": "cross-year-forum",
        "invitations": ["ICLR.cc/2024/Conference/-/Decision"],
        "content": {"decision": "Accept (Poster)"},
    }
    papers, _incomplete = OpenReviewSource.parse_notes_with_incomplete(
        [cross_year, decision],
        venue_id="ICLR.cc/2025/Conference",
        response=response,
    )
    assert papers == []

    legacy = {
        "id": "legacy-id",
        "forum": "legacy-forum",
        "content": {
            "title": "Legacy Refreshed Paper",
            "authors": ["Alice Example"],
            "abstract": "Legacy refreshed abstract.",
            "venue": "ICLR 2025 Conference (Poster)",
        },
    }

    class V2:
        def get_notes(self, **kwargs):
            if "id" in kwargs:
                return [legacy]
            return []

    class V1:
        def get_notes(self, **_kwargs):
            return []

    class Factory:
        def get(self, version: str):
            return V2() if version == "v2" else V1()

    refreshed = OpenReviewSource(Factory()).fetch_by_id("legacy-forum")
    assert refreshed.paper_id == "openreview:legacy-forum"
    assert refreshed.venue == "International Conference on Learning Representations"


def test_openreview_missing_venueid_cannot_use_conflicting_legacy_final_year() -> None:
    response = HttpResponse(
        url="https://api2.openreview.net/notes",
        final_url="https://api2.openreview.net/notes",
        status=200,
        headers={},
        body=b"{}",
    )
    submission = {
        "id": "legacy-cross-year-submission",
        "forum": "legacy-cross-year-forum",
        "content": {
            "title": "Legacy Cross Year Paper",
            "authors": ["Alice Example"],
            "abstract": "A legacy record with a conflicting final year.",
            "venue": "ICLR 2024 Conference (Poster)",
        },
    }
    accepted = {
        "id": "legacy-cross-year-decision",
        "forum": "legacy-cross-year-forum",
        "invitations": ["ICLR.cc/2024/Conference/-/Decision"],
        "content": {"decision": "Accept (Poster)"},
    }

    papers, _incomplete = OpenReviewSource.parse_notes_with_incomplete(
        [submission, accepted],
        venue_id="ICLR.cc/2025/Conference",
        response=response,
    )

    assert papers == []


def test_headless_fallback_cannot_expand_the_baseline_host_registry() -> None:
    with pytest.raises(HeadlessDiscoveryError, match="not registered"):
        _normalized_allowed_hosts({"example.invalid"})


def test_crossref_mailto_is_redacted_from_provenance_urls() -> None:
    value = _safe_url("https://api.crossref.org/works?query=security&mailto=person%40example.com")

    assert "person%40example.com" not in value
    assert "mailto=%3Credacted%3E" in value


def test_official_download_link_to_bibtex_is_not_treated_as_pdf() -> None:
    root = _tree('<a href="/biblio/export/bibtex/309953">Download</a>')

    assert _pdf_url(root, "https://www.usenix.org/conference/usenixsecurity25/presentation/adida") == ""


def test_official_citation_pdf_metadata_remains_preferred() -> None:
    root = _tree(
        '<meta name="citation_pdf_url" content="https://cdn.example.org/paper.pdf">'
        '<a href="/biblio/export/bibtex/309953">Download</a>'
    )

    assert _pdf_url(root, "https://www.usenix.org/conference/usenixsecurity25/presentation/adida") == "https://cdn.example.org/paper.pdf"


def test_official_download_link_to_real_pdf_remains_accepted() -> None:
    root = _tree('<a href="/paper.pdf">Download</a>')

    assert _pdf_url(root, "https://www.usenix.org/conference/usenixsecurity25/presentation/adida") == "https://www.usenix.org/paper.pdf"


def test_formal_deduplication_requires_strict_identity_proof() -> None:
    canonical = _paper(
        paper_id="official:paper", source="official", source_id="paper",
        title="Unicode-Safe Paper", authors=["Alice Example", "Bob Example"], doi="10.1234/same",
    )
    duplicate = _paper(
        paper_id="crossref:paper", source="crossref", source_id="paper",
        title="Different Display Title", authors=["Carol Example"], doi="10.1234/same",
    )
    near_match = _paper(
        paper_id="ieee_xplore:paper", source="ieee_xplore", source_id="paper",
        title="Unicode-Safe Paper", authors=["Alice Example", "Carol Example"], doi=None,
    )
    reports: list[dict] = []

    retained = pipeline._deduplicate_formal_records([canonical, duplicate, near_match], reports)

    assert [paper.paper_id for paper in retained] == [canonical.paper_id, near_match.paper_id]
    assert reports == [{
        "source": "formal_dedup",
        "adapter": "formal_dedup",
        "status": "duplicate",
        "canonical_id": canonical.paper_id,
        "duplicate_id": duplicate.paper_id,
        "method": "doi_exact",
    }]


def test_general_indexes_do_not_receive_arxiv_field_prefixes() -> None:
    query = 'abs:"jailbreak" OR abs:"prompt injection" AND ti:security'

    assert discovery_query_for_general_index(query) == '"jailbreak" OR "prompt injection" AND security'


@pytest.mark.parametrize(
    ("adapter", "plan", "expected_venue"),
    [
        (
            CrossrefSource,
            SearchPlan(queries=["security"], sources=["crossref"], crossref_venues=["ieee-sp"]),
            "ieee-sp",
        ),
        (
            IeeeXploreSource,
            SearchPlan(queries=["security"], sources=["ieee_xplore"], venue_groups=["ieee-sp"]),
            "ieee-sp",
        ),
    ],
)
@pytest.mark.parametrize("body", [b"", b"{}"])
def test_formal_source_empty_or_malformed_response_is_reported(adapter, plan, expected_venue, body) -> None:
    response = HttpResponse(
        url="https://source.invalid/search",
        final_url="https://source.invalid/search",
        status=200,
        headers={},
        body=body,
    )
    source = adapter(_ResponseClient(response), api_key="test-key") if adapter is IeeeXploreSource else adapter(_ResponseClient(response))

    result = source.discover_result(plan)

    assert result.papers == []
    assert result.incomplete == []
    report = result.reports[0]
    assert report["venue_group"] == expected_venue
    assert report["status"] == "error"
    assert report["errors"][0]["error_type"] in {"JSONDecodeError", "ValueError"}


@pytest.mark.parametrize(
    ("adapter", "plan"),
    [
        (CrossrefSource, SearchPlan(queries=["security"], sources=["crossref"], crossref_venues=["ieee-sp"])),
        (IeeeXploreSource, SearchPlan(queries=["security"], sources=["ieee_xplore"], venue_groups=["ieee-sp"])),
    ],
)
@pytest.mark.parametrize("error", [HttpRequestError(429, "https://source.invalid/search"), RuntimeError("client unavailable")])
def test_formal_source_client_errors_remain_visible_in_reports(adapter, plan, error) -> None:
    source = adapter(_ErrorClient(error), api_key="test-key") if adapter is IeeeXploreSource else adapter(_ErrorClient(error))

    result = source.discover_result(plan)

    assert result.papers == []
    report = result.reports[0]
    assert report["status"] == "error"
    assert report["requests_attempted"] == 1
    assert report["requests_failed"] == 1
    assert report["errors"][0]["error_type"] == type(error).__name__
    assert report["errors"][0]["message"]
    assert report["errors"][0]["http_status"] == getattr(error, "code", None)


@pytest.mark.parametrize("missing", ["abstract", "pdf_url"])
def test_crossref_missing_abstract_or_pdf_stays_incomplete(missing) -> None:
    item = _crossref_item()
    if missing == "abstract":
        item.pop("abstract")
    else:
        item["link"] = []

    papers, incomplete = CrossrefSource.parse_items_with_incomplete(
        _crossref_response(item), expected_venue="ieee-sp"
    )

    assert papers == []
    assert incomplete[0]["reason"] == "required_crossref_field_missing"
    assert missing in incomplete[0]["missing"]


def test_crossref_unknown_publication_date_remains_unknown() -> None:
    item = _crossref_item()
    item.pop("published")

    papers, incomplete = CrossrefSource.parse_items_with_incomplete(
        _crossref_response(item), expected_venue="ieee-sp"
    )

    assert incomplete == []
    assert len(papers) == 1
    assert papers[0].published_at is None


def test_crossref_discovery_clips_api_over_return_to_venue_budget() -> None:
    first = _crossref_item()
    second = _crossref_item()
    second["DOI"] = "10.1109/SP.2026.7654321"
    second["title"] = ["Crossref second title"]
    response = HttpResponse(
        url="https://api.crossref.org/works",
        final_url="https://api.crossref.org/works",
        status=200,
        headers={},
        body=json.dumps({"message": {"items": [first, second]}}).encode(),
    )

    result = CrossrefSource(_ResponseClient(response)).discover_result(SearchPlan(
        queries=["security"],
        sources=["crossref"],
        crossref_venues=["ieee-sp"],
        max_results_per_query=1,
        max_results_per_venue=1,
    ))

    assert len(result.papers) == 1
    assert result.reports[0]["records_valid"] == 1
    assert result.reports[0]["records_filtered"] == 1
    assert result.reports[0]["truncated"] is True


@pytest.mark.parametrize("missing", ["abstract", "pdf_url"])
def test_ieee_missing_abstract_or_pdf_stays_incomplete(missing) -> None:
    article = _ieee_article()
    article.pop(missing)

    papers, incomplete, _stats = IeeeXploreSource.parse_articles(
        _ieee_response(article), spec=get_venue_spec("ieee-sp")
    )

    assert papers == []
    assert incomplete[0]["reason"] == "required_ieee_field_missing"
    assert missing in incomplete[0]["missing"]


@pytest.mark.parametrize("value", ["2026-01-02", "02 January 2026", "January 2, 2026"])
def test_ieee_uses_only_an_explicit_complete_publication_date(value) -> None:
    article = _ieee_article()
    article["publication_date"] = value

    papers, incomplete, _stats = IeeeXploreSource.parse_articles(
        _ieee_response(article), spec=get_venue_spec("ieee-sp")
    )

    assert incomplete == []
    assert papers[0].published_at == "2026-01-02T00:00:00Z"


def test_ieee_year_is_not_promoted_to_a_publication_day() -> None:
    article = _ieee_article()
    article["publication_year"] = "2026"

    papers, incomplete, _stats = IeeeXploreSource.parse_articles(
        _ieee_response(article), spec=get_venue_spec("ieee-sp")
    )

    assert incomplete == []
    assert papers[0].published_at is None


def test_ieee_discovery_pages_requests_above_api_record_cap() -> None:
    class _PagingClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str, **_kwargs) -> HttpResponse:
            self.calls.append(url)
            params = parse_qs(urlparse(url).query)
            start = int(params["start_record"][0])
            limit = int(params["max_records"][0])
            assert start in {1, 101}
            articles = []
            for index in range(start, min(start + limit, 111)):
                article = _ieee_article()
                article.update({
                    "doi": f"10.1109/SP.2026.{index:07d}",
                    "title": f"IEEE authoritative title {index}",
                    "article_number": str(1230000 + index),
                    "html_url": f"https://ieeexplore.ieee.org/document/{1230000 + index}",
                    "pdf_url": f"https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber={1230000 + index}",
                })
                articles.append(article)
            return HttpResponse(
                url=url,
                final_url=url,
                status=200,
                headers={},
                body=json.dumps({"articles": articles}).encode(),
            )

    client = _PagingClient()
    result = IeeeXploreSource(client, api_key="test-key").discover_result(SearchPlan(
        queries=["security"],
        sources=["ieee_xplore"],
        venue_groups=["ieee-sp"],
        max_results_per_query=110,
        max_results_per_venue=110,
    ))

    assert len(result.papers) == 110
    assert [int(parse_qs(urlparse(url).query)["max_records"][0]) for url in client.calls] == [100, 10]
    assert [int(parse_qs(urlparse(url).query)["start_record"][0]) for url in client.calls] == [1, 101]
    assert result.reports[0]["records_scanned"] == 110
    assert result.reports[0]["records_valid"] == 110


def test_ieee_discovery_clips_api_over_return_to_venue_budget() -> None:
    first = _ieee_article()
    second = _ieee_article()
    second.update({
        "doi": "10.1109/SP.2026.7654321",
        "title": "IEEE second title",
        "article_number": "7654321",
        "html_url": "https://ieeexplore.ieee.org/document/7654321",
        "pdf_url": "https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=7654321",
    })
    response = HttpResponse(
        url="https://ieeexploreapi.ieee.org/api/v1/search/articles",
        final_url="https://ieeexploreapi.ieee.org/api/v1/search/articles",
        status=200,
        headers={},
        body=json.dumps({"articles": [first, second]}).encode(),
    )

    result = IeeeXploreSource(_ResponseClient(response), api_key="test-key").discover_result(SearchPlan(
        queries=["security"],
        sources=["ieee_xplore"],
        venue_groups=["ieee-sp"],
        max_results_per_query=1,
        max_results_per_venue=1,
    ))

    assert len(result.papers) == 1
    assert result.reports[0]["records_valid"] == 1
    assert result.reports[0]["records_filtered"] == 1
    assert result.reports[0]["truncated"] is True


def test_missing_bibtex_is_rejected_before_materialization(monkeypatch, tmp_path) -> None:
    doi = "10.1234/no-bibtex"
    paper = _paper(
        paper_id=f"doi:{doi}", source="crossref", source_id=doi,
        title="No BibTeX Paper", authors=["Alice Example"], doi=doi,
    )
    monkeypatch.setattr(pipeline, "refresh_authoritative", lambda paper, **_kwargs: paper)
    monkeypatch.setattr(pipeline, "fetch_bibtex", lambda *_args, **_kwargs: ("", "", {}))
    monkeypatch.setattr(pipeline, "fetch_fulltext", lambda *_args, **_kwargs: {"sha256": "a" * 64, "path": "content.txt"})

    facts, manifest = pipeline.materialize(
        candidates_payload={"candidates": [paper.to_dict()]},
        selections=[SelectionEntry(paper.paper_id, 1.0, "Security", "ranked", "core")],
        data_dir=tmp_path,
        target=1,
        scholar_limit=0,
    )

    assert facts["total"] == 0
    assert manifest["rejected"][0]["reason"] == "verification_failed"
    assert "authoritative BibTeX unavailable" in manifest["rejected"][0]["message"]


def test_openreview_v1_compatibility_keeps_v2_challenge_visible() -> None:
    class ChallengeError(Exception):
        code = 403

    class V2:
        def get_notes(self, **_kwargs):
            raise ChallengeError("Challenge verification required")

    class V1:
        def get_notes(self, **kwargs):
            assert kwargs["invitation"] == "ICLR.cc/2025/Conference/-/Submission"
            return [{"id": "paper-id", "content": {"title": "Paper"}}]

    class Factory:
        def get(self, version: str):
            return V2() if version == "v2" else V1()

    report = OpenReviewSource(Factory()).probe("ICLR.cc/2025/Conference")

    assert report["status"] == "partial"
    assert report["fallback_errors"] == [{
        "endpoint": "v2",
        "stage": "challenge",
        "error_type": "ChallengeError",
        "http_status": 403,
    }]


def test_openreview_v2_challenge_uses_fixed_http_recovery_and_keeps_evidence() -> None:
    class ChallengeError(Exception):
        code = 403

    class V2:
        def get_notes(self, **_kwargs):
            raise ChallengeError("Challenge verification required")

    class V1:
        def get_notes(self, **_kwargs):
            raise AssertionError("challenge recovery must not route through v1")

    class Factory:
        def get(self, version: str):
            return V2() if version == "v2" else V1()

    class RecoveryClient:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return HttpResponse(
                url=url,
                final_url=url,
                status=200,
                headers={"content-type": "application/json"},
                body=json.dumps({"notes": [{"id": "recovered-paper"}]}).encode(),
            )

    recovery = RecoveryClient()
    report = OpenReviewSource(Factory(), http_client=recovery).probe("ICLR.cc/2025/Conference")

    assert report["status"] == "partial"
    assert report["notes"] == 1
    assert report["fallback_errors"] == [{
        "endpoint": "v2",
        "stage": "challenge",
        "error_type": "ChallengeError",
        "http_status": 403,
    }]
    assert recovery.calls[0][0] == (
        "https://api2.openreview.net/notes?content.venueid=ICLR.cc%2F2025%2FConference"
        "&limit=1&details=replies"
    )
    assert recovery.calls[0][1]["allowed_hosts"] == {
        "api2.openreview.net",
        "openreview.net",
    }


@pytest.mark.parametrize("message", ["Forbidden", "authentication required"])
def test_openreview_v2_auth_403_does_not_trigger_http_recovery(message: str) -> None:
    class Auth403Error(Exception):
        code = 403

    class V2:
        def get_notes(self, **_kwargs):
            raise Auth403Error(message)

    class V1:
        def get_notes(self, **_kwargs):
            return [{"id": "legacy-paper", "content": {"title": "Paper"}}]

    class Factory:
        def get(self, version: str):
            return V2() if version == "v2" else V1()

    class RecoveryClient:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            raise AssertionError("auth/forbidden 403 must not invoke v2 recovery")

    recovery = RecoveryClient()
    report = OpenReviewSource(Factory(), http_client=recovery).probe("ICLR.cc/2025/Conference")

    assert report["status"] == "partial"
    assert report["client_version"] == "v1"
    assert report["fallback_errors"][0]["stage"] == "auth"
    assert recovery.calls == []


@pytest.mark.parametrize(
    "body",
    [b"not-json", json.dumps({"notes": {"id": "wrong-shape"}}).encode()],
)
def test_openreview_v2_http_recovery_failure_preserves_challenge_error(body: bytes) -> None:
    class ChallengeError(Exception):
        code = 403

    class V2:
        def get_notes(self, **_kwargs):
            raise ChallengeError("Challenge verification required")

    class V1:
        def get_notes(self, **_kwargs):
            raise AssertionError("failed challenge recovery must not route through v1")

    class Factory:
        def get(self, version: str):
            return V2() if version == "v2" else V1()

    class RecoveryClient:
        def get(self, url, **_kwargs):
            return HttpResponse(url=url, status=200, headers={}, body=body)

    result = OpenReviewSource(Factory(), http_client=RecoveryClient()).discover_result(
        SearchPlan(queries=["security"], openreview_venues=["ICLR.cc/2025/Conference"], max_results_per_venue=1)
    )

    errors = [item for item in result.reports[0]["errors"] if "endpoint" in item]
    assert result.papers == []
    assert result.reports[0]["status"] == "error"
    assert [item["endpoint"] for item in errors] == ["v2", "v2_http_recovery"]
    assert errors[0]["stage"] == "challenge"
    assert errors[0]["http_status"] == 403


def test_openreview_v2_html_challenge_recovery_stays_visible() -> None:
    class ChallengeError(Exception):
        code = 403

    class V2:
        def get_notes(self, **_kwargs):
            raise ChallengeError("Challenge verification required")

    class Factory:
        def get(self, _version: str):
            return V2()

    class RecoveryClient:
        def get(self, url, **_kwargs):
            return HttpResponse(
                url=url,
                final_url="https://openreview.net/challenge",
                status=200,
                headers={"content-type": "text/html"},
                body=b"<html>challenge</html>",
            )

    source = OpenReviewSource(Factory(), http_client=RecoveryClient())
    with pytest.raises(ChallengeError):
        source.probe("ICLR.cc/2025/Conference")

    assert source.errors[-1]["endpoint"] == "v2_http_recovery"
    assert source.errors[-1]["stage"] == "challenge"


def test_materialization_enforces_the_five_paper_track_limit(monkeypatch, tmp_path) -> None:
    papers = []
    selections = []
    for index in range(6):
        doi = f"10.1234/test{index}"
        paper = _paper(
            paper_id=f"doi:{doi}", source="crossref", source_id=doi,
            title=f"Formal Paper {index}", authors=["Alice Example"], doi=doi,
        )
        papers.append(paper)
        selections.append(SelectionEntry(paper.paper_id, 1.0, "Security", "ranked", "core"))

    monkeypatch.setattr(pipeline, "refresh_authoritative", lambda paper, **_kwargs: paper)
    monkeypatch.setattr(pipeline, "fetch_bibtex", lambda paper, **_kwargs: (
        f"@article{{test, title={{{paper.title}}}, author={{Alice Example}}}}",
        "https://doi.org/example",
        {},
    ))
    monkeypatch.setattr(pipeline, "fetch_fulltext", lambda *_args, **_kwargs: {"sha256": "a" * 64, "path": "content.txt"})

    facts, manifest = pipeline.materialize(
        candidates_payload={"candidates": [paper.to_dict() for paper in papers]},
        selections=selections,
        data_dir=tmp_path,
        scholar_limit=0,
    )

    assert facts["total"] == 5
    assert manifest["collection"]["track_counts"] == {"core": 5, "broad": 0}
    assert manifest["rejected"][-1] == {
        "paper_id": papers[-1].paper_id,
        "reason": "track_quota_exceeded",
        "track": "core",
        "limit": 5,
    }


def test_core_selection_requires_declared_core_keyword(monkeypatch, tmp_path) -> None:
    doi = "10.1234/broad-only"
    paper = _paper(
        paper_id=f"doi:{doi}", source="crossref", source_id=doi,
        title="General security paper", authors=["Alice Example"], doi=doi,
    )
    monkeypatch.setattr(pipeline, "refresh_authoritative", lambda paper, **_kwargs: paper)
    monkeypatch.setattr(pipeline, "fetch_bibtex", lambda paper, **_kwargs: (
        f"@article{{test, title={{{paper.title}}}, author={{Alice Example}}}}",
        "https://doi.org/example",
        {},
    ))
    monkeypatch.setattr(pipeline, "fetch_fulltext", lambda *_args, **_kwargs: {"sha256": "a" * 64, "path": "content.txt"})

    facts, manifest = pipeline.materialize(
        candidates_payload={
            "candidates": [paper.to_dict()],
            "plan": {"core_keywords": ["prompt injection"]},
        },
        selections=[SelectionEntry(paper.paper_id, 1.0, "Security", "ranked", "core")],
        data_dir=tmp_path,
        target=1,
        scholar_limit=0,
    )

    assert facts["total"] == 0
    assert manifest["rejected"] == [{"paper_id": paper.paper_id, "reason": "core_keyword_mismatch"}]


@pytest.mark.parametrize(
    ("candidate_abstract", "authoritative_abstract", "published"),
    (
        ("Injected prompt injection claim.", "Authoritative unrelated abstract.", False),
        ("Candidate unrelated abstract.", "Authoritative prompt injection abstract.", True),
    ),
)
def test_core_keyword_match_uses_authoritative_abstract(
    monkeypatch, tmp_path, candidate_abstract: str, authoritative_abstract: str, published: bool,
) -> None:
    doi = "10.1234/authoritative-abstract"
    candidate = _paper(
        paper_id=f"doi:{doi}", source="crossref", source_id=doi,
        title="Authoritative title", authors=["Alice Example"], doi=doi,
    )
    candidate.abstract = candidate_abstract
    authoritative = _paper(
        paper_id=f"doi:{doi}", source="crossref", source_id=doi,
        title="Authoritative title", authors=["Alice Example"], doi=doi,
    )
    authoritative.abstract = authoritative_abstract
    monkeypatch.setattr(pipeline, "refresh_authoritative", lambda *_args, **_kwargs: authoritative)
    monkeypatch.setattr(pipeline, "fetch_bibtex", lambda paper, **_kwargs: (
        f"@article{{test, title={{{paper.title}}}, author={{Alice Example}}}}",
        "https://doi.org/example",
        {},
    ))
    monkeypatch.setattr(pipeline, "fetch_fulltext", lambda *_args, **_kwargs: {"sha256": "a" * 64, "path": "content.txt"})

    facts, manifest = pipeline.materialize(
        candidates_payload={
            "candidates": [candidate.to_dict()],
            "plan": {"core_keywords": ["prompt injection"]},
        },
        selections=[SelectionEntry(candidate.paper_id, 1.0, "Security", "ranked", "core")],
        data_dir=tmp_path,
        target=1,
        scholar_limit=0,
    )

    assert facts["total"] == int(published)
    if published:
        assert manifest["rejected"] == []
    else:
        assert manifest["rejected"] == [{"paper_id": candidate.paper_id, "reason": "core_keyword_mismatch"}]
