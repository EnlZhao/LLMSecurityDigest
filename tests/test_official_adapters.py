import json
from urllib.parse import parse_qs, urlparse

import pytest

from llm_security_digest.papers.http import HttpResponse
from llm_security_digest.papers.models import PaperFacts, SearchPlan, get_venue_spec
from llm_security_digest.papers.official import (
    AAAIOJSAdapter,
    ACLAnthologyAdapter,
    CVFAdapter,
    ECVAAdapter,
    IEEEComputerCSDLAdapter,
    IJCAIAdapter,
    NDSSAdapter,
    NeurIPSAdapter,
    PMLRAdapter,
    USENIXAdapter,
)
from llm_security_digest.papers.sources import CrossrefSource, official_route_for_paper


def test_acl_volume_links_are_bound_to_anthology_host() -> None:
    html = """
    <a href="/2025.acl-long.1/">official</a>
    <a href="https://attacker.example/2025.acl-long.2/">foreign</a>
    """

    assert ACLAnthologyAdapter.volume_paper_urls(html, year=2025, volume_key="acl-long") == [
        "https://aclanthology.org/2025.acl-long.1/"
    ]


def test_pmlr_paper_links_are_bound_to_proceedings_host() -> None:
    html = """
    <a href="/v267/official.html">official</a>
    <a href="https://attacker.example/v267/foreign.html">foreign</a>
    """

    assert PMLRAdapter.paper_urls(html, volume="v267") == [
        "https://proceedings.mlr.press/v267/official.html"
    ]


def test_pmlr_volume_links_are_bound_to_proceedings_host() -> None:
    html = """
    <li><a href="/v267/">ICML 2025</a></li>
    <li><a href="https://attacker.example/v268/">ICML 2025</a></li>
    """

    assert PMLRAdapter.volume_index(html) == [{
        "year": 2025,
        "volume": "v267",
        "url": "https://proceedings.mlr.press/v267/",
    }]


def test_neurips_paper_links_are_bound_to_proceedings_host() -> None:
    html = """
    <a href="/paper_files/paper/2024/hash/official-Abstract-Conference.html">official</a>
    <a href="https://attacker.example/paper_files/paper/2024/hash/foreign-Abstract-Conference.html">foreign</a>
    """

    assert NeurIPSAdapter.paper_urls(html, year=2024) == [
        "https://proceedings.neurips.cc/paper_files/paper/2024/hash/official-Abstract-Conference.html"
    ]


def test_aaai_issue_pagination_and_article_links_are_host_bound() -> None:
    archive = """
    <div class="obj_issue_summary">
      <a class="title" href="/index.php/AAAI/issue/view/1">AAAI-26 Technical Tracks 1</a>
    </div>
    <div class="obj_issue_summary">
      <a class="title" href="https://attacker.example/index.php/AAAI/issue/view/2">AAAI-26 Technical Tracks 2</a>
    </div>
    <a class="next" href="https://attacker.example/index.php/AAAI/issue/archive?page=2">Next</a>
    <a class="next" href="/index.php/AAAI/issue/archive?page=2">Next</a>
    """
    entries = AAAIOJSAdapter.issue_page_entries(archive)

    assert [entry["url"] for entry in entries] == [
        "https://ojs.aaai.org/index.php/AAAI/issue/view/1"
    ]
    assert AAAIOJSAdapter.next_archive_url(
        archive, base_url=AAAIOJSAdapter.ARCHIVE_URL
    ) == "https://ojs.aaai.org/index.php/AAAI/issue/archive?page=2"

    articles = """
    <div class="obj_article_summary">
      <h3 class="title"><a href="/index.php/AAAI/article/view/1">Official paper</a></h3>
      <a href="/index.php/AAAI/article/view/1/2">PDF</a>
    </div>
    <div class="obj_article_summary">
      <h3 class="title"><a href="https://attacker.example/index.php/AAAI/article/view/2">Foreign paper</a></h3>
      <a href="https://attacker.example/index.php/AAAI/article/view/2/3">PDF</a>
    </div>
    """
    summaries = AAAIOJSAdapter.article_summaries(
        articles, issue={"year": 2026}
    )

    assert summaries == [{
        "article_id": "1",
        "url": "https://ojs.aaai.org/index.php/AAAI/article/view/1",
        "title": "Official paper",
        "pdf_url": "https://ojs.aaai.org/index.php/AAAI/article/view/1/2",
        "authors": [],
        "year": 2026,
    }]


def test_ijcai_paper_links_cannot_route_detail_fetches_off_host() -> None:
    html = """
    <div class="paper_wrapper">
      <div class="title">Official paper</div><div class="authors">Alice Example</div>
      <div class="abstract">Official abstract.</div>
      <a href="0001.pdf">PDF</a><a href="/proceedings/2024/1">Details</a>
    </div>
    <div class="paper_wrapper">
      <div class="title">Foreign paper</div><div class="authors">Mallory Example</div>
      <div class="abstract">Foreign abstract.</div>
      <a href="https://attacker.example/proceedings/2024/2.pdf">PDF</a>
      <a href="https://attacker.example/proceedings/2024/2">Details</a>
    </div>
    """
    records = IJCAIAdapter.parse_papers(
        html,
        spec=get_venue_spec("ijcai"),
        year=2024,
        base_url="https://www.ijcai.org/proceedings/2024/",
    )

    assert [record.paper.landing_url for record in records if record.paper] == [
        "https://www.ijcai.org/proceedings/2024/1"
    ]


def test_ijcai_detail_fetches_obey_discovery_budget() -> None:
    html = "".join(
        f'''<div class="paper_wrapper">
          <div class="title">Paper {number}</div><div class="authors">Author {number}</div>
          <a href="{number:04d}.pdf">PDF</a><a href="/proceedings/2024/{number}">Details</a>
        </div>'''
        for number in range(1, 4)
    )
    fetched: list[str] = []

    def load_detail(url: str) -> tuple[str, object]:
        fetched.append(url)
        return (
            "<meta name=\"citation_title\" content=\"Verified paper\">"
            "<meta name=\"citation_author\" content=\"Alice Example\">"
            "<meta name=\"citation_pdf_url\" content=\"https://www.ijcai.org/proceedings/2024/0001.pdf\">"
            "<meta name=\"description\" content=\"Verified abstract.\">",
            None,
        )

    records = IJCAIAdapter.parse_papers(
        html,
        spec=get_venue_spec("ijcai"),
        year=2024,
        base_url="https://www.ijcai.org/proceedings/2024/",
        detail_loader=load_detail,
        max_records=1,
    )

    assert len(records) == 1
    assert fetched == ["https://www.ijcai.org/proceedings/2024/1"]


def test_usenix_presentation_links_are_bound_to_usenix_host() -> None:
    html = """
    <a href="/conference/usenixsecurity24/presentation/official">official</a>
    <a href="https://attacker.example/conference/usenixsecurity24/presentation/foreign">foreign</a>
    """

    assert USENIXAdapter.presentation_urls(
        html,
        year=2024,
        base_url="https://www.usenix.org/conference/usenixsecurity24/technical-sessions",
    ) == ["https://www.usenix.org/conference/usenixsecurity24/presentation/official"]


def test_ndss_paper_links_are_bound_to_ndss_host() -> None:
    html = """
    <a href="/ndss-paper/official/">official</a>
    <a href="https://attacker.example/ndss-paper/foreign/">foreign</a>
    """

    assert NDSSAdapter.paper_urls(
        html,
        base_url="https://www.ndss-symposium.org/ndss2024/accepted-papers/",
    ) == ["https://www.ndss-symposium.org/ndss-paper/official/"]


def test_ndss_doi_resolution_requires_exact_crossref_identity() -> None:
    paper = PaperFacts(
        paper_id="ndss:2025:official",
        source="ndss",
        source_id="2025:official",
        title="Exact NDSS Paper",
        authors=["Alice Example", "Bob Example"],
        abstract="Official abstract.",
        publication_status="published",
        venue="Network and Distributed System Security Symposium",
        published_at=None,
        updated_at=None,
        doi=None,
        landing_url="https://www.ndss-symposium.org/ndss-paper/official/",
        pdf_url="https://www.ndss-symposium.org/wp-content/uploads/official.pdf",
        collection_tier="formal",
        match_state="canonical",
    )

    class Client:
        def get(self, url, **_kwargs):
            return HttpResponse(
                url=url,
                final_url=url,
                status=200,
                headers={},
                body=json.dumps({"message": {"items": [{
                    "DOI": "10.14722/ndss.2025.230001",
                    "title": ["Exact NDSS Paper"],
                    "author": [
                        {"given": "Alice", "family": "Example"},
                        {"given": "Bob", "family": "Example"},
                    ],
                    "container-title": ["Proceedings 2025 Network and Distributed System Security Symposium"],
                    "type": "proceedings-article",
                }]}}).encode(),
            )

    doi, provenance = CrossrefSource(Client()).resolve_ndss_doi(paper)

    assert doi == "10.14722/ndss.2025.230001"
    assert provenance["source"] == "crossref_ndss_exact_doi"


def test_ndss_doi_resolution_rejects_title_or_author_mismatch() -> None:
    paper = PaperFacts(
        paper_id="ndss:2025:official",
        source="ndss",
        source_id="2025:official",
        title="Exact NDSS Paper",
        authors=["Alice Example", "Bob Example"],
        abstract="Official abstract.",
        publication_status="published",
        venue="Network and Distributed System Security Symposium",
        published_at=None,
        updated_at=None,
        doi=None,
        landing_url="https://www.ndss-symposium.org/ndss-paper/official/",
        pdf_url="https://www.ndss-symposium.org/wp-content/uploads/official.pdf",
        collection_tier="formal",
        match_state="canonical",
    )

    class Client:
        def get(self, url, **_kwargs):
            return HttpResponse(
                url=url,
                status=200,
                headers={},
                body=json.dumps({"message": {"items": [{
                    "DOI": "10.14722/ndss.2025.230001",
                    "title": ["Different NDSS Paper"],
                    "author": [{"given": "Alice", "family": "Example"}],
                    "container-title": ["Proceedings 2025 Network and Distributed System Security Symposium"],
                    "type": "proceedings-article",
                }]}}).encode(),
            )

    with pytest.raises(ValueError, match="0 exact matches"):
        CrossrefSource(Client()).resolve_ndss_doi(paper)


def test_cvpr_parser_already_requires_registered_host() -> None:
    html = """
    <a href="/content/CVPR2024/html/official.html">official</a>
    <a href="https://attacker.example/content/CVPR2024/html/foreign.html">foreign</a>
    """

    assert CVFAdapter.paper_urls(
        html, spec=get_venue_spec("cvpr"), year=2024
    ) == ["https://openaccess.thecvf.com/content/CVPR2024/html/official.html"]


def test_eccv_parser_already_requires_registered_host() -> None:
    html = """
    <a href="/papers/eccv_2024/papers_ECCV/html/official_ECCV_2024_paper.php">official</a>
    <a href="https://attacker.example/papers/eccv_2024/papers_ECCV/html/foreign_ECCV_2024_paper.php">foreign</a>
    """

    assert ECVAAdapter.paper_urls(
        html, spec=get_venue_spec("eccv"), year=2024
    ) == [
        "https://www.ecva.net/papers/eccv_2024/papers_ECCV/html/official_ECCV_2024_paper.php"
    ]


def test_cvpr_inline_bibref_is_preserved_for_authoritative_bibtex() -> None:
    record = CVFAdapter.parse_paper(
        '''<meta name="citation_title" content="Official CVPR Paper">
        <meta name="citation_author" content="Alice Example">
        <meta name="citation_abstract" content="Official abstract.">
        <a href="/content/CVPR2024/papers/official.pdf">PDF</a>
        <div class="bibref pre-white-space">@InProceedings{Example2024,
        title={Official CVPR Paper}, author={Alice Example}}</div>''',
        spec=get_venue_spec("cvpr"),
        url="https://openaccess.thecvf.com/content/CVPR2024/html/official.html",
        year=2024,
    )

    assert record.paper is not None
    assert record.paper.source_metadata["bibtex_inline"].startswith("@InProceedings")


class _CSDLClient:
    def __init__(self, *, pdf_body: bytes = b"%PDF-1.7\n", include_front_matter: bool = False) -> None:
        self.pdf_body = pdf_body
        self.include_front_matter = include_front_matter
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs) -> HttpResponse:
        self.calls.append(url)
        parsed = urlparse(url)
        if parsed.path.endswith("/pdf"):
            return HttpResponse(
                url=url,
                final_url="https://csdl-downloads.ieeecomputer.org/signed/article.pdf?signature=test",
                status=200,
                headers={"content-type": "application/pdf"},
                body=self.pdf_body,
            )
        values = parse_qs(parsed.query)
        query = values.get("query", [""])[0]
        variables = json.loads(values.get("variables", ["{}"])[0])
        if "proceedings(groupId" in query:
            payload = {"data": {"proceedings": [{
                "id": "21B7ONGXzZ6",
                "acronym": "SP",
                "title": "IEEE Symposium on Security and Privacy",
                "year": 2025,
            }]}}
        elif "articlesByProceedingWithPagination" in query:
            article_results = [
                {"id": "OA1", "idPrefix": "sp", "fno": "223600a001", "pubType": "proceedings", "year": 2025},
                {"id": "C1", "idPrefix": "sp", "fno": "223600a002", "pubType": "proceedings", "year": 2025},
            ]
            if self.include_front_matter:
                article_results.insert(0, {
                    "id": "FRONT", "idPrefix": "sp", "fno": "223600z001",
                    "pubType": "proceedings", "title": "Title Page", "year": 2025,
                })
            payload = {"data": {
                "proceeding": {"id": "21B7ONGXzZ6", "groupId": "1000646", "acronym": "sp", "year": 2025},
                "articlesByProceeding": {
                    "totalResults": len(article_results),
                    "articleResults": article_results,
                },
            }}
        elif "articleById" in query:
            article_id = variables["articleId"]
            open_access = article_id == "OA1"
            payload = {"data": {
                "proceeding": {"id": "21B7ONGXzZ6", "groupId": "1000646", "acronym": "sp", "year": 2025},
                "article": {
                    "id": article_id,
                    "idPrefix": "sp",
                    "fno": "223600a001" if open_access else "223600a002",
                    "year": 2025,
                    "pubType": "proceedings",
                    "title": "OA title" if open_access else "Closed title",
                    "authors": [{"fullName": "Alice Example" if open_access else "Bob Example"}],
                    "abstract": "<p>OA abstract.</p>" if open_access else "Closed abstract.",
                    "doi": "10.1109/SP61157.2025.00037" if open_access else None,
                    "isOpenAccess": open_access,
                    "hasPdf": True,
                },
            }}
        else:
            raise AssertionError(f"unexpected CSDL request: {url}")
        return HttpResponse(
            url=url,
            final_url=url,
            status=200,
            headers={"content-type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
        )


def _csdl_plan(*, limit: int = 2) -> SearchPlan:
    return SearchPlan(
        queries=["security"],
        venue_groups=["ieee-sp"],
        identifiers={"years": [2025]},
        max_results_per_query=100,
        max_results_per_venue=limit,
    )


def test_csdl_graphql_reuses_verified_endpoint_but_rebuilds_query() -> None:
    class Hint:
        source = "official"
        adapter = "ieee_csdl"
        route_kind = "index"
        url = "https://www.computer.org/csdl/api/v1/graphql?query=stale&variables=%7B%7D"

    class Catalog:
        def verified_routes(self, *, venue):
            assert venue.key == "ieee-sp"
            return [Hint()]

    client = _CSDLClient()
    adapter = IEEEComputerCSDLAdapter(client, route_catalog=Catalog())
    adapter._graphql(
        "query ($groupId: String) { proceedings(groupId: $groupId) { id } }",
        {"groupId": "1000646"},
    )

    assert len(client.calls) == 1
    requested = urlparse(client.calls[0])
    assert requested.path == "/csdl/api/v1/graphql"
    assert parse_qs(requested.query)["variables"] == ['{"groupId":"1000646"}']


def test_csdl_source_id_route_is_strict_and_registered() -> None:
    source_id = "2025:sp:223600a037:21B7QqhuoOA"
    assert IEEEComputerCSDLAdapter.article_url(source_id) == (
        "https://www.computer.org/csdl/proceedings-article/sp/2025/223600a037/21B7QqhuoOA"
    )
    with pytest.raises(ValueError):
        IEEEComputerCSDLAdapter.parse_source_id(source_id + "?redirect=https://attacker.example")
    with pytest.raises(ValueError):
        IEEEComputerCSDLAdapter.parse_source_id("2025:evil:223600a037:21B7QqhuoOA")
    with pytest.raises(ValueError):
        IEEEComputerCSDLAdapter.parse_source_id("2025:sp:223600z005:21B7QqhuoOA")
    with pytest.raises(ValueError):
        IEEEComputerCSDLAdapter.parse_source_id("2000:sp:0665vii:article2000")
    assert IEEEComputerCSDLAdapter.parse_source_id("2020:sp:349700b452:article2020") == (
        2020, "sp", "349700b452", "article2020",
    )
    assert IEEEComputerCSDLAdapter.parse_source_id("2010:sp:05504784:article2010") == (
        2010, "sp", "05504784", "article2010",
    )
    paper = PaperFacts(
        paper_id=f"ieee_csdl:{source_id}", source="ieee_csdl", source_id=source_id,
        title="Title", authors=["Alice Example"], abstract="Abstract",
        publication_status="published", venue=get_venue_spec("ieee-sp").name,
        published_at=None, updated_at=None, doi=None,
        landing_url=IEEEComputerCSDLAdapter.article_url(source_id),
        pdf_url=IEEEComputerCSDLAdapter.pdf_url("21B7QqhuoOA"),
    )
    spec_key, url, hosts = official_route_for_paper(paper)
    assert spec_key == "ieee-sp"
    assert url == paper.landing_url
    assert hosts == frozenset({"www.computer.org", "csdl-downloads.ieeecomputer.org"})


def test_csdl_discovery_requires_open_access_and_defers_pdf_fetch() -> None:
    client = _CSDLClient()
    result = IEEEComputerCSDLAdapter(client).discover(_csdl_plan(), get_venue_spec("ieee-sp"))

    assert [paper.paper_id for paper in result.papers] == ["ieee_csdl:2025:sp:223600a001:OA1"]
    paper = result.papers[0]
    assert paper.pdf_url == "https://www.computer.org/csdl/pds/api/csdl/proceedings/download-article/OA1/pdf"
    assert "pdf_verification" not in paper.source_metadata
    assert "csdl-downloads.ieeecomputer.org" not in json.dumps(paper.to_dict())
    assert "signature" not in json.dumps(paper.to_dict())
    assert result.incomplete[0]["reason"] == "official_pdf_access_gated"
    assert result.incomplete[0]["missing"] == ["pdf_url"]
    assert sum(urlparse(url).path.endswith("/pdf") for url in client.calls) == 0


def test_csdl_front_matter_is_filtered_without_consuming_candidate_budget() -> None:
    client = _CSDLClient(include_front_matter=True)
    result = IEEEComputerCSDLAdapter(client).discover(_csdl_plan(limit=1), get_venue_spec("ieee-sp"))

    assert [paper.paper_id for paper in result.papers] == ["ieee_csdl:2025:sp:223600a001:OA1"]
    assert result.incomplete == []
    report = result.reports[0]
    assert report["records_scanned"] == 2
    assert report["records_filtered"] == 1
    assert report["filtered"] == 1
    assert report["raw_scan_cap"] == IEEEComputerCSDLAdapter.RAW_SCAN_CAP
    assert sum(urlparse(url).path.endswith("/pdf") for url in client.calls) == 0


@pytest.mark.parametrize("fno", ["313000z001", "223600z005", "941400z001", "0665vii", "0665viii"])
def test_csdl_front_matter_rule_uses_fno_document_class(fno: str) -> None:
    assert IEEEComputerCSDLAdapter._is_front_matter({"pubType": "proceedings", "fno": fno})
    assert not IEEEComputerCSDLAdapter._is_front_matter({"pubType": "article", "fno": fno})


@pytest.mark.parametrize("fno", ["313000a001", "349700b452", "05504784"])
def test_csdl_source_id_accepts_non_front_matter_document_classes(fno: str) -> None:
    assert IEEEComputerCSDLAdapter.parse_source_id(f"2024:sp:{fno}:article2024")[2] == fno


def test_csdl_pdf_verification_remains_available_for_materialization() -> None:
    client = _CSDLClient()
    fixed_url, response, verification = IEEEComputerCSDLAdapter(client)._verify_pdf("OA1")

    assert fixed_url == "https://www.computer.org/csdl/pds/api/csdl/proceedings/download-article/OA1/pdf"
    assert verification["verified_pdf"] is True
    assert verification["host"] == "csdl-downloads.ieeecomputer.org"
    assert "final_url" not in verification
    assert "signature" not in json.dumps(verification)
    assert response.body.startswith(b"%PDF-")


def test_csdl_non_pdf_response_stays_unverified_for_materialization() -> None:
    client = _CSDLClient(pdf_body=b"<html>login required</html>")

    with pytest.raises(ValueError, match="official_pdf_access_gated"):
        IEEEComputerCSDLAdapter(client)._verify_pdf("OA1")


def _csdl_detail_record(*, proceeding: dict | None = None, pub_type: str = "proceedings", doi: str | None = "10.1109/SP61157.2025.00037"):
    return IEEEComputerCSDLAdapter(_CSDLClient())._record_from_detail(
        spec=get_venue_spec("ieee-sp"),
        article={
            "id": "OA1", "idPrefix": "sp", "fno": "223600a001", "year": 2025,
            "pubType": pub_type, "title": "OA title", "authors": [{"fullName": "Alice Example"}],
            "abstract": "OA abstract.", "doi": doi, "isOpenAccess": True, "hasPdf": True,
        },
        proceeding=proceeding if proceeding is not None else {"id": "21B7ONGXzZ6", "groupId": "1000646", "acronym": "sp", "year": 2025},
        response=HttpResponse(url="https://www.computer.org/csdl/api/v1/graphql", final_url="https://www.computer.org/csdl/api/v1/graphql", status=200, headers={}, body=b"{}"),
        expected_source_id="2025:sp:223600a001:OA1",
        expected_proceeding_id="21B7ONGXzZ6",
    )


@pytest.mark.parametrize("proceeding", [
    {},
    {"id": "wrong", "groupId": "1000646", "acronym": "sp", "year": 2025},
    {"id": "21B7ONGXzZ6", "groupId": "other", "acronym": "sp", "year": 2025},
])
def test_csdl_detail_requires_exact_registered_proceeding(proceeding: dict) -> None:
    record = _csdl_detail_record(proceeding=proceeding)

    assert record.paper is None
    assert record.incomplete["reason"] == "official_identity_mismatch"


def test_csdl_detail_requires_proceedings_type_and_doi() -> None:
    wrong_type = _csdl_detail_record(pub_type="journal")
    missing_doi = _csdl_detail_record(doi=None)

    assert wrong_type.paper is None
    assert wrong_type.incomplete["reason"] == "official_document_type_invalid"
    assert missing_doi.paper is None
    assert missing_doi.incomplete["reason"] == "official_doi_missing"
