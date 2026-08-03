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
