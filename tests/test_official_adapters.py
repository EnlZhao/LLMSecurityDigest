from llm_security_digest.papers.models import get_venue_spec
from llm_security_digest.papers.official import (
    AAAIOJSAdapter,
    ACLAnthologyAdapter,
    CVFAdapter,
    ECVAAdapter,
    IJCAIAdapter,
    NDSSAdapter,
    NeurIPSAdapter,
    PMLRAdapter,
    USENIXAdapter,
)


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
