from llm_security_digest.papers.http import HttpResponse
from llm_security_digest.papers.models import PaperFacts, SelectionEntry
from llm_security_digest.papers.openreview_client import openreview_failure_stage
from llm_security_digest.papers import pipeline
from llm_security_digest.papers.sources import ArxivSource, OpenReviewSource, reconcile_arxiv_to_formal


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
