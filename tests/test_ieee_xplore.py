import json

from llm_security_digest.papers.http import HttpResponse
from llm_security_digest.papers.models import SearchPlan, get_venue_spec
from llm_security_digest.papers.sources import IeeeXploreSource


def _article(**changes):
    value = {
        "doi": "10.1109/TDSC.2026.1234567",
        "title": "A Dependable Security Study",
        "authors": [{"full_name": "Ada Lovelace"}],
        "abstract": "The authoritative abstract.",
        "publication_title": "IEEE Transactions on Dependable and Secure Computing",
        "publication_year": 2026,
        "html_url": "https://ieeexplore.ieee.org/document/1234567",
        "pdf_url": "https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=1234567",
    }
    value.update(changes)
    return value


def test_ieee_xplore_parser_requires_registered_identity_fields():
    body = json.dumps({"articles": [_article()]}).encode()
    response = HttpResponse("https://ieeexploreapi.ieee.org/api/v1/search/articles", 200, {}, body)
    papers, incomplete, stats = IeeeXploreSource.parse_articles(response, spec=get_venue_spec("tdsc"))
    assert len(papers) == 1
    assert not incomplete
    assert stats == {"scanned": 1, "filtered": 0}
    assert papers[0].source == "ieee_xplore"
    assert papers[0].venue == get_venue_spec("tdsc").name

    missing_abstract = HttpResponse(response.url, 200, {}, json.dumps({"articles": [_article(abstract="")]}).encode())
    papers, incomplete, _ = IeeeXploreSource.parse_articles(missing_abstract, spec=get_venue_spec("tdsc"))
    assert not papers
    assert incomplete[0]["reason"] == "required_ieee_field_missing"
    assert "abstract" in incomplete[0]["missing"]


def test_ieee_xplore_missing_key_is_visible_and_does_not_synthesize_records(monkeypatch):
    monkeypatch.delenv("IEEE_XPLORE_API_KEY", raising=False)
    result = IeeeXploreSource(object()).discover_result(
        SearchPlan(queries=["security"], venue_groups=["tdsc"], sources=["ieee_xplore"])
    )
    assert not result.papers
    assert result.reports[0]["status"] == "error"
    assert result.reports[0]["stage"] == "auth"
    assert result.reports[0]["error_type"] == "missing_api_key"


def test_ieee_xplore_request_redacts_api_key_in_http_provenance():
    seen = {}

    class Client:
        def get(self, url, **kwargs):
            seen["url"] = url
            seen["provenance_url"] = kwargs["provenance_url"]
            return HttpResponse(kwargs["provenance_url"], 200, {}, json.dumps({"articles": []}).encode())

    source = IeeeXploreSource(Client(), api_key="private-key")
    result = source.discover_result(
        SearchPlan(queries=["security"], venue_groups=["tdsc"], sources=["ieee_xplore"])
    )
    assert result.reports[0]["status"] == "ok"
    assert "private-key" in seen["url"]
    assert "private-key" not in seen["provenance_url"]
