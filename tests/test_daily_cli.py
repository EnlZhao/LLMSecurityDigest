from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from llm_security_digest.papers import pipeline
from llm_security_digest.papers.content import persist_content
from llm_security_digest.papers.http import HttpResponse
from llm_security_digest.papers.models import DiscoveryResult, PaperFacts


def _daily_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "llm_security" / "run_daily.py"
    spec = importlib.util.spec_from_file_location("test_daily_cli_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_doctor_does_not_require_optional_serpapi_key(monkeypatch, tmp_path, capsys) -> None:
    daily = _daily_module()

    class Client:
        def get(self, url, **_kwargs):
            return HttpResponse(url=url, final_url=url, status=200, headers={}, body=b"{}")

    client = Client()
    openreview_clients = []

    class OpenReview:
        def __init__(self, *, http_client):
            openreview_clients.append(http_client)

        def probe(self, _venue_id):
            return {"status": "ok", "http_status": 200}

    monkeypatch.setattr(daily, "default_client", lambda: client)
    monkeypatch.setattr(daily, "OpenReviewSource", OpenReview)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    assert daily.command_doctor(argparse.Namespace(data_dir=str(tmp_path))) == 0
    assert openreview_clients == [client]
    payload = json.loads(capsys.readouterr().out)
    assert next(item for item in payload["checks"] if item["name"] == "serpapi") == {
        "name": "serpapi",
        "status": "optional_missing",
    }


def test_baseline_cli_workflow_is_bounded_and_authoritative(monkeypatch, tmp_path, capsys) -> None:
    daily = _daily_module()
    data_dir = tmp_path / "llmsd-data"
    plan_path = tmp_path / "plan.json"
    candidates_path = tmp_path / "candidates.json"
    selection_path = tmp_path / "selection.json"
    facts_path = tmp_path / "facts.json"
    manifest_path = tmp_path / "manifest.json"
    monkeypatch.setenv("LLMSD_DATA_DIR", str(data_dir))
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    class MockClient:
        def get(self, url, **_kwargs):
            return HttpResponse(url=url, final_url=url, status=200, headers={}, body=b"{}")

    doctor_client = MockClient()
    openreview_clients = []

    class MockDoctorOpenReview:
        def __init__(self, *, http_client):
            openreview_clients.append(http_client)

        def probe(self, _venue_id):
            return {"status": "ok", "http_status": 200}

    monkeypatch.setattr(daily, "default_client", lambda: doctor_client)
    monkeypatch.setattr(daily, "OpenReviewSource", MockDoctorOpenReview)
    monkeypatch.setattr(pipeline, "default_client", lambda: MockClient())

    def invoke(*argv: str) -> int:
        monkeypatch.setattr(sys, "argv", ["run_daily.py", *argv])
        return daily.main()

    assert invoke("doctor") == 0
    assert openreview_clients == [doctor_client]
    doctor = json.loads(capsys.readouterr().out)
    assert all(check["status"] in {"ok", "optional_missing"} for check in doctor["checks"])
    assert data_dir.exists()

    assert invoke("init-plan", "--out", str(plan_path)) == 0
    capsys.readouterr()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["target"] == 10

    def make_candidate(index: int) -> PaperFacts:
        doi = f"10.1234/fixture-{index:02d}"
        return PaperFacts(
            paper_id=f"doi:{doi}",
            source="crossref",
            source_id=doi,
            title=("Prompt Injection Security Fixture Paper" if index < 5 else "Security Fixture Paper") + f" {index}",
            authors=["Alice Example"],
            abstract=f"LLM_FACT_INJECTION_{index} security candidate abstract",
            publication_status="published",
            venue="ieee-sp",
            published_at="2026-01-02T00:00:00Z",
            updated_at=None,
            doi=doi,
            landing_url=f"https://doi.org/{doi}",
            pdf_url=f"https://ieeexplore.ieee.org/document/{index}.pdf",
            source_comment=f"LLM_FACT_INJECTION_COMMENT_{index}",
            source_metadata={"llm_fact": f"LLM_FACT_INJECTION_METADATA_{index}"},
            collection_tier="formal",
            match_state="canonical",
        )

    candidate_papers = [make_candidate(index) for index in range(10)]

    class MockSource:
        name = "mock"

        def __init__(self, *_args, **_kwargs):
            pass

        def discover_result(self, _plan):
            return DiscoveryResult(
                reports=[{"source": self.name, "adapter": "mock", "status": "ok"}]
            )

    class MockOfficialSource(MockSource):
        name = "official"

        def discover_result(self, _plan):
            return DiscoveryResult(
                papers=list(candidate_papers),
                reports=[{"source": self.name, "adapter": "mock", "status": "ok"}],
            )

    class MockOpenReviewSource(MockSource):
        name = "openreview"

    class MockCrossrefSource(MockSource):
        name = "crossref"

    class MockIeeeSource(MockSource):
        name = "ieee_xplore"

    class MockArxivSource(MockSource):
        name = "arxiv"

    monkeypatch.setattr(pipeline, "OfficialSource", MockOfficialSource)
    monkeypatch.setattr(pipeline, "OpenReviewSource", MockOpenReviewSource)
    monkeypatch.setattr(pipeline, "CrossrefSource", MockCrossrefSource)
    monkeypatch.setattr(pipeline, "IeeeXploreSource", MockIeeeSource)
    monkeypatch.setattr(pipeline, "ArxivSource", MockArxivSource)

    assert invoke(
        "collect",
        "--plan",
        str(plan_path),
        "--out",
        str(candidates_path),
    ) == 0
    capsys.readouterr()
    candidates_payload = json.loads(candidates_path.read_text(encoding="utf-8"))
    assert candidates_payload["total"] == 10
    assert candidates_payload["plan"]["target"] == 10
    assert len(candidates_payload["candidates"]) == 10

    selections = {
        "selections": [
            {
                "paper_id": paper.paper_id,
                "score": float(10 - index),
                "category": "Security",
                "reason": "script-owned fixture ranking",
                "track": "core" if index < 5 else "broad",
            }
            for index, paper in enumerate(candidate_papers)
        ]
    }
    selection_path.write_text(json.dumps(selections), encoding="utf-8")

    def mock_refresh(paper: PaperFacts, *, client) -> PaperFacts:
        index = int(paper.source_id.rsplit("-", 1)[-1])
        return PaperFacts(
            paper_id=paper.paper_id,
            source=paper.source,
            source_id=paper.source_id,
            title=paper.title,
            authors=list(paper.authors),
            abstract=f"Authoritative abstract {index}.",
            publication_status="published",
            venue="IEEE Symposium on Security and Privacy",
            published_at=paper.published_at,
            updated_at=paper.updated_at,
            doi=paper.doi,
            landing_url=paper.landing_url,
            pdf_url=paper.pdf_url,
            collection_tier="formal",
            match_state="canonical",
        )

    def mock_bibtex(paper: PaperFacts, *, client):
        value = (
            f"@article{{fixture, title={{{paper.title}}}, author={{Example, Alice}}, "
            f"doi={{{paper.doi}}}}}"
        )
        return value, paper.landing_url, {"source": "mock"}

    def mock_fulltext(paper: PaperFacts, *, client, data_dir: Path, max_bytes: int):
        intro = f"{paper.title}\nIntroduction\nVerified security text for {paper.paper_id}."
        methods = "\nMethods\nThe authoritative method confirms security evidence."
        text = intro + methods
        return persist_content(
            data_dir=data_dir,
            paper_id=paper.paper_id,
            body=text.encode("utf-8"),
            extracted_text=text,
            sections=[
                {"id": "intro", "title": "Introduction", "offset": 0},
                {"id": "methods", "title": "Methods", "offset": len(intro)},
            ],
            extension="txt",
            source_url=paper.pdf_url,
        )

    monkeypatch.setattr(pipeline, "refresh_authoritative", mock_refresh)
    monkeypatch.setattr(pipeline, "fetch_bibtex", mock_bibtex)
    monkeypatch.setattr(pipeline, "fetch_fulltext", mock_fulltext)

    assert invoke(
        "materialize",
        "--candidates",
        str(candidates_path),
        "--selection",
        str(selection_path),
        "--facts",
        str(facts_path),
        "--manifest",
        str(manifest_path),
    ) == 0
    capsys.readouterr()
    facts = json.loads(facts_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert facts["total"] == 10
    assert manifest["collection"]["track_counts"] == {"core": 5, "broad": 5}
    assert manifest["collection"]["track_limits"] == {"core": 5, "broad": 5}
    assert all(
        "LLM_FACT_INJECTION" not in json.dumps(paper)
        for paper in facts["papers"]
    )
    assert all(paper["abstract"].startswith("Authoritative abstract") for paper in facts["papers"])

    first_id = candidate_papers[0].paper_id
    assert invoke("outline", "--facts", str(facts_path), "--paper-id", first_id) == 0
    outline = json.loads(capsys.readouterr().out)
    assert [section["id"] for section in outline] == ["intro", "methods"]

    assert invoke(
        "read-section",
        "--facts",
        str(facts_path),
        "--paper-id",
        first_id,
        "--section-id",
        "intro",
    ) == 0
    section = capsys.readouterr().out
    assert "Verified security text" in section

    assert invoke(
        "find",
        "--facts",
        str(facts_path),
        "--paper-id",
        first_id,
        "--query",
        "security",
    ) == 0
    matches = json.loads(capsys.readouterr().out)
    assert matches["paper_id"] == first_id
    assert matches["matches"]


def test_evolution_cli_errors_do_not_echo_exception_details(monkeypatch, tmp_path, capsys) -> None:
    daily = _daily_module()
    secret = "TOP_SECRET_VALUE"

    class FailingStore:
        def save_candidate(self, _candidate):
            raise RuntimeError(secret)

        def load_candidate(self, _candidate):
            raise RuntimeError(secret)

        def shadow(self, _candidate):
            raise RuntimeError(secret)

        def activate(self, _candidate, *, report):
            raise RuntimeError(secret)

        def rollback(self, _version):
            raise RuntimeError(secret)

    monkeypatch.setattr(daily, "_evolution_store", lambda _data_dir: FailingStore())
    commands = [
        (daily.command_reflect, argparse.Namespace(input=None, out=None, data_dir=str(tmp_path))),
        (daily.command_validate_evolution, argparse.Namespace(candidate=None, version="candidate", data_dir=str(tmp_path))),
        (daily.command_shadow_evolution, argparse.Namespace(candidate=None, version="candidate", data_dir=str(tmp_path))),
        (daily.command_activate_evolution, argparse.Namespace(candidate=None, version="candidate", shadow_report=None, data_dir=str(tmp_path))),
        (daily.command_rollback_evolution, argparse.Namespace(version="candidate", data_dir=str(tmp_path))),
    ]

    for command, args in commands:
        assert command(args) == 2
        output = capsys.readouterr().out
        assert secret not in output
        report = json.loads(output)
        assert report["error"] == "evolution operation failed; details are withheld"


def test_scholar_workflow_uses_bounded_candidate_intake_and_explicit_artifact() -> None:
    root = Path(__file__).resolve().parents[1]
    scholar_workflow = (root / ".github" / "workflows" / "probe-scholar.yml").read_text(encoding="utf-8")
    collect_workflow = (root / ".github" / "workflows" / "collect-candidates.yml").read_text(encoding="utf-8")

    assert "MAX_ARTIFACT_BYTES = 5 * 1024 * 1024" in scholar_workflow
    assert "MAX_TITLE_LENGTH = 1000" in scholar_workflow
    assert "duplicate paper_id values" in scholar_workflow
    assert "path: ${{ runner.temp }}/llmsd/candidates.json" in collect_workflow
    assert "path: ${{ runner.temp }}/llmsd/\n" not in collect_workflow


def test_segment_reading_defaults_are_conservative() -> None:
    daily = _daily_module()

    read_section = daily.build_parser().parse_args([
        "read-section",
        "--facts",
        "facts.json",
        "--paper-id",
        "paper",
        "--section-id",
        "intro",
    ])
    find = daily.build_parser().parse_args([
        "find",
        "--facts",
        "facts.json",
        "--paper-id",
        "paper",
        "--query",
        "security",
    ])

    assert read_section.max_chars == daily.MAX_SECTION_CHARS == 6_000
    assert find.limit == daily.MAX_FIND_LIMIT == 3
    assert find.context == daily.MAX_FIND_CONTEXT == 300


@pytest.mark.parametrize(
    ("command", "option"),
    [
        ("read-section", "--max-chars"),
        ("find", "--limit"),
        ("find", "--context"),
    ],
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_segment_reading_parser_rejects_non_positive_values(command, option, value) -> None:
    daily = _daily_module()
    parser = daily.build_parser()
    common = [command, "--facts", "facts.json", "--paper-id", "paper"]
    if command == "read-section":
        common += ["--section-id", "intro"]
    else:
        common += ["--query", "security"]

    with pytest.raises(SystemExit):
        parser.parse_args(common + [option, value])


@pytest.mark.parametrize(
    ("command", "option", "maximum"),
    [
        ("read-section", "--max-chars", "MAX_SECTION_CHARS"),
        ("find", "--limit", "MAX_FIND_LIMIT"),
        ("find", "--context", "MAX_FIND_CONTEXT"),
    ],
)
def test_segment_reading_parser_rejects_oversized_values(command, option, maximum) -> None:
    daily = _daily_module()
    parser = daily.build_parser()
    common = [command, "--facts", "facts.json", "--paper-id", "paper"]
    if command == "read-section":
        common += ["--section-id", "intro"]
    else:
        common += ["--query", "security"]
    with pytest.raises(SystemExit):
        parser.parse_args(common + [option, str(getattr(daily, maximum) + 1)])


@pytest.mark.parametrize(
    ("command", "attribute", "maximum"),
    [
        ("read-section", "max_chars", "MAX_SECTION_CHARS"),
        ("find", "limit", "MAX_FIND_LIMIT"),
        ("find", "context", "MAX_FIND_CONTEXT"),
    ],
)
@pytest.mark.parametrize("value_kind", ["zero", "negative", "oversized"])
def test_segment_reading_commands_reject_invalid_values_before_content_reading(
    monkeypatch, command, attribute, maximum, value_kind
) -> None:
    daily = _daily_module()
    common = {
        "facts": Path("facts.json"),
        "paper_id": "paper",
        "section_id": "intro",
        "query": "security",
        "max_chars": daily.MAX_SECTION_CHARS,
        "limit": daily.MAX_FIND_LIMIT,
        "context": daily.MAX_FIND_CONTEXT,
        "data_dir": None,
    }
    bound = getattr(daily, maximum)
    common[attribute] = {"zero": 0, "negative": -1, "oversized": bound + 1}[value_kind]
    common["func"] = daily.command_read_section if command == "read-section" else daily.command_find
    args = argparse.Namespace(**common)

    def fail_if_read(*_args, **_kwargs):
        raise AssertionError("paper content must not be read for invalid bounds")

    monkeypatch.setattr(daily, "_load_paper", fail_if_read)
    with pytest.raises(ValueError, match="must be an integer between 1"):
        args.func(args)


def test_find_parser_rejects_oversized_query() -> None:
    daily = _daily_module()
    with pytest.raises(SystemExit):
        daily.build_parser().parse_args([
            "find",
            "--facts",
            "facts.json",
            "--paper-id",
            "paper",
            "--query",
            "q" * (daily.MAX_FIND_QUERY_CHARS + 1),
        ])


def test_find_rejects_oversized_query_before_content_reading(monkeypatch) -> None:
    daily = _daily_module()
    args = argparse.Namespace(
        facts=Path("facts.json"),
        paper_id="paper",
        query="q" * (daily.MAX_FIND_QUERY_CHARS + 1),
        limit=daily.MAX_FIND_LIMIT,
        context=daily.MAX_FIND_CONTEXT,
        data_dir=None,
    )

    def fail_if_read(*_args, **_kwargs):
        raise AssertionError("paper content must not be read for an invalid query")

    monkeypatch.setattr(daily, "_load_paper", fail_if_read)
    with pytest.raises(ValueError, match="must be at most"):
        daily.command_find(args)
