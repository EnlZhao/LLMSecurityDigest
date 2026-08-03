from datetime import date
import json

from llm_security_digest import prompt, config
from llm_security_digest.papers.http import HttpResponse
from llm_security_digest.papers.http import HttpClient
from llm_security_digest.papers.content import extract_pdf, persist_content
from llm_security_digest.papers.models import PaperFacts, SearchPlan, SelectionEntry, normalize_title
from llm_security_digest.papers.pipeline import (
    _same_discovered_facts,
    _matches_date_window,
    load_analysis,
    materialize,
    refresh_authoritative,
    validate_bibtex,
)
from llm_security_digest.papers.sources import ArxivSource, GoogleScholarEnricher, OpenReviewSource


def test_prompt_contains_date_and_run_dir():
    today = date(2026, 8, 2)
    run = config.PROJECT_ROOT / "cache" / "run-abc"
    text = prompt.build_prompt(run_dir=run, today=today)
    assert "2026-08-02" in text
    assert "run-abc" in text
    assert "LLM Security Daily" in text
    assert "jailbreak" in text.lower() or "prompt injection" in text.lower()


def test_prompt_forbids_deletion():
    run = config.PROJECT_ROOT / "cache" / "run-abc"
    text = prompt.build_prompt(run_dir=run, today=date.today())
    assert "never create or modify title" in text.lower()
    assert "curl" in text.lower()
    assert "fewer is correct" in text.lower()


def _paper(**changes):
    values = {
        "paper_id": "arxiv:2404.01833",
        "source": "arxiv",
        "source_id": "2404.01833",
        "title": "Strict Facts for LLM Security",
        "authors": ["Ada Lovelace", "Alan Turing"],
        "abstract": "A sufficiently complete authoritative abstract.",
        "publication_status": "preprint",
        "venue": None,
        "published_at": "2026-08-01T00:00:00Z",
        "updated_at": None,
        "doi": None,
        "landing_url": "https://arxiv.org/abs/2404.01833",
        "pdf_url": "https://arxiv.org/pdf/2404.01833",
    }
    values.update(changes)
    return PaperFacts(**values)


def test_arxiv_atom_parser_keeps_authoritative_fields():
    body = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>
      <id>http://arxiv.org/abs/2404.01833v2</id><updated>2026-08-02T00:00:00Z</updated>
      <published>2026-08-01T00:00:00Z</published><title>Strict Facts for LLM Security</title>
      <summary>Original abstract from the Atom response.</summary>
      <author><name>Ada Lovelace</name></author><category term="cs.CR"/>
      <arxiv:primary_category term="cs.CR"/></entry></feed>'''
    response = HttpResponse("https://export.arxiv.org/api/query", 200, {}, body)
    papers = ArxivSource.parse_feed(response)
    assert len(papers) == 1
    assert papers[0].source_id == "2404.01833"
    assert papers[0].title == "Strict Facts for LLM Security"
    assert papers[0].abstract == "Original abstract from the Atom response."


def test_materialize_refreshes_candidate_facts_from_authoritative_id():
    body = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>
      <id>http://arxiv.org/abs/2404.01833v2</id><updated>2026-08-02T00:00:00Z</updated>
      <published>2026-08-01T00:00:00Z</published><title>Authoritative Title</title>
      <summary>Authoritative abstract from the second lookup.</summary>
      <author><name>Ada Lovelace</name></author></entry></feed>'''

    class Client:
        def get(self, url, **kwargs):
            return HttpResponse(url, 200, {}, body)

    tampered = _paper(title="LLM supplied title", abstract="LLM supplied abstract")
    refreshed = refresh_authoritative(tampered, client=Client())
    assert refreshed.title == "Authoritative Title"
    assert refreshed.abstract == "Authoritative abstract from the second lookup."


def test_openreview_parser_excludes_rejected_notes():
    notes = [
        {"id": "accepted", "forum": "accepted", "cdate": 1, "content": {
            "venueid": {"value": "ICLR.cc/2026/Conference"},
            "venue": {"value": "ICLR 2026 poster"}, "title": {"value": "Accepted Paper"},
            "abstract": {"value": "Authoritative abstract"}, "authors": {"value": ["A. Author"]}}},
        {"id": "rejected", "forum": "rejected", "content": {
            "venueid": {"value": "ICLR.cc/2026/Conference"},
            "venue": {"value": "ICLR 2026 rejected submission"}, "title": {"value": "Rejected Paper"},
            "abstract": {"value": "Abstract"}, "authors": {"value": ["B. Author"]}}},
        {"id": "pending", "forum": "pending", "cdate": 1, "content": {
            "venueid": {"value": "ICLR.cc/2026/Conference"},
            "venue": {"value": "ICLR 2026 Conference Submission"}, "title": {"value": "Pending Paper"},
            "abstract": {"value": "Abstract"}, "authors": {"value": ["C. Author"]}}},
        {"id": "missing-venue", "forum": "missing-venue", "cdate": 1, "content": {
            "venueid": {"value": "ICLR.cc/2026/Conference"},
            "title": {"value": "Missing Venue Paper"}, "abstract": {"value": "Abstract"},
            "authors": {"value": ["D. Author"]}}},
    ]
    response = HttpResponse("https://api2.openreview.net/notes", 200, {}, b"{}")
    papers = OpenReviewSource.parse_notes(notes, venue_id="ICLR.cc/2026/Conference", response=response)
    assert [paper.source_id for paper in papers] == ["accepted"]


def test_scholar_enrichment_requires_one_exact_title_and_redacts_key():
    class Client:
        def get(self, url, **kwargs):
            assert "secret-key" in url
            assert "secret-key" not in kwargs["provenance_url"]
            payload = {"organic_results": [{"title": "Strict Facts for LLM Security", "result_id": "x"}]}
            return HttpResponse(kwargs["provenance_url"], 200, {}, json.dumps(payload).encode())

    result = GoogleScholarEnricher(Client(), api_key="secret-key").enrich(_paper())
    assert result["status"] == "matched"
    assert "secret-key" not in result["source_url"]


def test_selection_and_analysis_reject_fact_fields(tmp_path):
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"selections": [{
        "paper_id": "arxiv:1", "score": 90, "category": "Test", "reason": "relevant",
        "title": "LLM supplied title",
    }]}))
    try:
        SelectionEntry.load_many(selection)
    except ValueError as exc:
        assert "forbidden fact fields" in str(exc)
    else:
        raise AssertionError("selection accepted an LLM-provided title")

    analysis = tmp_path / "analysis.json"
    analysis.write_text(json.dumps({"papers": [{"paper_id": "arxiv:1", "bibtex": "invented"}]}))
    try:
        load_analysis(analysis, {"arxiv:1"})
    except ValueError as exc:
        assert "forbidden fact fields" in str(exc)
    else:
        raise AssertionError("analysis accepted LLM-provided BibTeX")


def test_bibtex_validation_is_strict_but_allows_latex_braces():
    valid = r'''@article{x, title={{Strict} Facts for {LLM} Security},
      author={Ada Lovelace and Alan Turing}, eprint={2404.01833}}'''
    validate_bibtex(_paper(), valid)
    try:
        validate_bibtex(_paper(), valid.replace("{Strict} Facts", "{Invented} Facts"))
    except ValueError as exc:
        assert "title does not match" in str(exc)
    else:
        raise AssertionError("mismatched BibTeX title was accepted")


def test_bibtex_validation_checks_every_author():
    valid = r'''@article{x, title={Strict Facts for LLM Security},
      author={Ada Lovelace and Alan Turing}, eprint={2404.01833}}'''
    validate_bibtex(_paper(), valid)
    tampered = valid.replace("Alan Turing", "Grace Hopper")
    try:
        validate_bibtex(_paper(), tampered)
    except ValueError as exc:
        assert "authors do not match" in str(exc)
    else:
        raise AssertionError("mismatched BibTeX author was accepted")


def test_normalize_title_preserves_non_latin_identity_and_latex_accents():
    assert normalize_title(r"{\"U}ber LLM Security") == normalize_title("Über LLM Security")
    assert normalize_title("大模型安全") == "大模型安全"
    assert normalize_title("大模型安全") != normalize_title("大模型安保")


def test_materialize_publishes_fewer_than_target_without_fallback(monkeypatch, tmp_path):
    good = _paper()
    bad = _paper(paper_id="arxiv:9999.00001", source_id="9999.00001")
    payload = {"candidates": [good.to_dict(), bad.to_dict()], "source_reports": []}
    selections = [
        SelectionEntry(good.paper_id, 100, "A", "good"),
        SelectionEntry(bad.paper_id, 90, "A", "bad"),
    ]

    def fake_bibtex(paper, client):
        if paper.paper_id == bad.paper_id:
            raise ValueError("authoritative BibTeX unavailable")
        return ("@article{x,title={Strict Facts for LLM Security},author={Ada Lovelace},eprint={2404.01833}}", "https://arxiv.org/bibtex/2404.01833", {})

    monkeypatch.setattr("llm_security_digest.papers.pipeline.fetch_bibtex", fake_bibtex)
    monkeypatch.setattr("llm_security_digest.papers.pipeline.refresh_authoritative", lambda paper, client: paper)
    monkeypatch.setattr("llm_security_digest.papers.pipeline.fetch_fulltext", lambda *args, **kwargs: {"sha256": "a" * 64, "path": "/verified.pdf"})
    facts, manifest = materialize(
        candidates_payload=payload, selections=selections, data_dir=tmp_path,
        target=10, scholar_limit=1, client=object(),
    )
    assert facts["total"] == 1
    assert manifest["shortfall"] == 9
    assert len(manifest["rejected"]) == 1


def _pdf_with_text(text: str) -> bytes:
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    body = document.tobytes()
    document.close()
    return body


def test_pdf_identity_validation_accepts_matching_title_and_rejects_wrong_title():
    matching = _pdf_with_text("Strict Facts for LLM Security\\nAda Lovelace")
    extracted, sections = extract_pdf(matching, "Strict Facts for LLM Security")
    assert "Strict Facts for LLM Security" in extracted
    assert sections[0]["id"] == "page-1"

    wrong = _pdf_with_text("A completely different paper")
    try:
        extract_pdf(wrong, "Strict Facts for LLM Security")
    except ValueError as exc:
        assert "title does not match" in str(exc)
    else:
        raise AssertionError("wrong PDF identity was accepted")


def test_pdf_identity_validation_rejects_empty_or_non_pdf_bytes():
    try:
        extract_pdf(b"", "Strict Facts for LLM Security")
    except ValueError as exc:
        assert "not a PDF" in str(exc)
    else:
        raise AssertionError("empty body was accepted as a PDF")


def test_persisted_content_paths_are_relative_to_data_dir(tmp_path):
    content = persist_content(
        data_dir=tmp_path,
        paper_id="arxiv:2404.01833",
        body=b"%PDF-test",
        extracted_text="verified text",
        sections=[{"id": "page-1", "title": "Page 1", "page": 1}],
        extension="pdf",
        source_url="https://arxiv.org/pdf/2404.01833",
    )
    assert content["path"].startswith("papers/arxiv_2404.01833/")
    assert not content["path"].startswith("/")
    assert content["text_path"].startswith("papers/arxiv_2404.01833/")
    assert content["outline_path"].startswith("papers/arxiv_2404.01833/")


def test_duplicate_source_hits_ignore_provenance_hashes():
    first = _paper(provenance={"title": {"response_sha256": "a"}})
    second = _paper(provenance={"title": {"response_sha256": "b"}})
    assert _same_discovered_facts(first, second)
    assert not _same_discovered_facts(first, _paper(title="Different authoritative title"))


def test_date_window_is_applied_to_all_sources():
    plan = SearchPlan(
        queries=["security"], date_from="2026-08-01", date_to="2026-08-02"
    )
    assert _matches_date_window(_paper(published_at="2026-08-01T00:00:00Z"), plan)
    assert not _matches_date_window(_paper(published_at="2026-07-31T00:00:00Z"), plan)
    assert not _matches_date_window(_paper(published_at=None), plan)


def test_http_errors_redact_serpapi_credentials(monkeypatch):
    from urllib.error import HTTPError
    import urllib.request

    def fail(*args, **kwargs):
        raise HTTPError(args[0].full_url, 401, "unauthorized", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    client = HttpClient(user_agent="test", retries=1)
    try:
        client.get(
            "https://serpapi.com/search.json?engine=google_scholar&api_key=secret-key",
            provenance_url="https://serpapi.com/search.json?engine=google_scholar",
        )
    except RuntimeError as exc:
        assert "secret-key" not in str(exc)
        assert "api_key" not in str(exc) or "<redacted>" not in str(exc)
    else:
        raise AssertionError("HTTP error was not raised")
