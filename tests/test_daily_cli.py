from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from llm_security_digest.papers.http import HttpResponse


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

    class OpenReview:
        def probe(self, _venue_id):
            return {"status": "ok", "http_status": 200}

    monkeypatch.setattr(daily, "default_client", lambda: Client())
    monkeypatch.setattr(daily, "OpenReviewSource", OpenReview)
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    assert daily.command_doctor(argparse.Namespace(data_dir=str(tmp_path))) == 0
    payload = json.loads(capsys.readouterr().out)
    assert next(item for item in payload["checks"] if item["name"] == "serpapi") == {
        "name": "serpapi",
        "status": "optional_missing",
    }


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
