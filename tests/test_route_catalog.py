from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from llm_security_digest.papers.http import HttpResponse
from llm_security_digest.papers.models import get_venue_spec
from llm_security_digest.papers.official import AAAIOJSAdapter, NeurIPSAdapter
from llm_security_digest.route_catalog import RouteCatalog


class FakeClient:
    def __init__(self, response: HttpResponse | Exception):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _daily_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "llm_security" / "run_daily.py"
    spec = importlib.util.spec_from_file_location("test_route_catalog_daily", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verified_route_persists_metadata_and_is_reusable(tmp_path) -> None:
    url = "https://www.usenix.org/conference/usenixsecurity26/presentation/example"
    client = FakeClient(HttpResponse(url=url, final_url=url, status=200, headers={}, body=b"route-body"))

    catalog = RouteCatalog(tmp_path)
    record = catalog.verify(
        venue="usenix-security",
        url=url,
        source="official",
        route_kind="landing",
        evidence_source="hermes",
        client=client,
    )

    assert record.verification_state == "verified"
    assert record.http_status == 200
    assert record.response_hash
    assert record.adapter == "usenix"
    assert record.first_verified_at == record.last_verified_at
    assert record.evidence_source == "hermes"
    assert client.calls[0][1]["allowed_hosts"] == frozenset({"www.usenix.org"})

    reopened = RouteCatalog(tmp_path)
    persisted = reopened.verified_routes(venue="USENIX Security")
    assert len(persisted) == 1
    assert persisted[0].to_dict()["response_sha256"] == record.response_hash
    assert reopened.reusable_route(venue="usenix-security", url=url) is not None


def test_failed_and_unregistered_routes_are_persisted_but_never_reused(tmp_path) -> None:
    url = "https://www.usenix.org/conference/usenixsecurity26/presentation/missing"
    failed_client = FakeClient(HttpResponse(url=url, final_url=url, status=503, headers={}, body=b"blocked"))
    catalog = RouteCatalog(tmp_path)

    failed = catalog.verify(venue="usenix-security", url=url, client=failed_client)
    assert failed.verification_state == "failed"
    assert failed.http_status == 503
    assert failed.last_verified_at is not None
    assert catalog.verified_routes(venue="usenix-security") == []
    assert catalog.reusable_route(venue="usenix-security", url=url) is None

    unregistered_client = FakeClient(HttpResponse(url="https://example.com", final_url="https://example.com", status=200, headers={}, body=b"no"))
    rejected = catalog.verify(venue="usenix-security", url="https://example.com/paper", client=unregistered_client)
    assert rejected.verification_state == "rejected"
    assert unregistered_client.calls == []
    assert "example.com" in rejected.error_message
    assert len(catalog.list_routes(venue="usenix-security")) == 2


def test_official_route_rejects_an_unregistered_adapter_label(tmp_path) -> None:
    catalog = RouteCatalog(tmp_path)
    client = FakeClient(HttpResponse(
        url="https://www.usenix.org/conference/usenixsecurity26/technical-sessions",
        final_url="https://www.usenix.org/conference/usenixsecurity26/technical-sessions",
        status=200,
        headers={},
        body=b"index",
    ))

    record = catalog.verify(
        venue="usenix-security",
        url="https://www.usenix.org/conference/usenixsecurity26/technical-sessions",
        source="official",
        adapter="wrong-adapter",
        route_kind="index",
        client=client,
    )

    assert record.verification_state == "rejected"
    assert client.calls == []
    assert "registered venue adapter" in (record.error_message or "")


def test_secret_and_private_routes_do_not_escape_data_dir(tmp_path) -> None:
    catalog = RouteCatalog(tmp_path / "runtime")
    client = FakeClient(HttpResponse(url="https://www.usenix.org", final_url="https://www.usenix.org", status=200, headers={}, body=b"no"))

    secret = catalog.verify(
        venue="usenix-security",
        url="https://www.usenix.org/paper?api_key=do-not-store",
        client=client,
    )
    private = catalog.verify(
        venue="usenix-security",
        url="https://127.0.0.1/paper",
        client=client,
    )
    assert secret.verification_state == "rejected"
    assert private.verification_state == "rejected"
    assert "do-not-store" not in json.dumps([item.to_dict() for item in catalog.list_routes()])
    assert catalog.db_path.parent == (tmp_path / "runtime").resolve()
    assert not (tmp_path / "facts.json").exists()
    assert not (tmp_path / "runtime" / "facts.json").exists()


def test_route_catalog_cli_verify_and_list(monkeypatch, tmp_path, capsys) -> None:
    daily = _daily_module()
    url = "https://www.usenix.org/conference/usenixsecurity26/presentation/cli"
    client = FakeClient(HttpResponse(url=url, final_url=url, status=200, headers={}, body=b"cli"))
    monkeypatch.setattr(daily, "default_client", lambda: client)
    monkeypatch.setattr(
        daily.sys,
        "argv",
        [
            "run_daily.py",
            "route-catalog",
            "verify",
            "--venue",
            "usenix-security",
            "--url",
            url,
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert daily.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verification_state"] == "verified"

    monkeypatch.setattr(
        daily.sys,
        "argv",
        ["run_daily.py", "route-catalog", "list", "--venue", "usenix-security", "--data-dir", str(tmp_path)],
    )
    assert daily.main() == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed["routes"]) == 1


def test_official_adapter_uses_only_matching_verified_index_hint() -> None:
    class Hint:
        source = "official"
        adapter = "neurips"
        route_kind = "index"
        url = "https://proceedings.neurips.cc/paper_files/paper/2025/"

    class Catalog:
        def verified_routes(self, *, venue):
            assert venue.key == "neurips"
            return [Hint()]

    class Client:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return HttpResponse(url=url, final_url=url, status=200, headers={}, body=b"index")

    client = Client()
    adapter = NeurIPSAdapter(client, route_catalog=Catalog())
    adapter._get(
        "https://proceedings.neurips.cc/paper_files/paper/2025",
        spec=get_venue_spec("neurips"),
        route_kind="index",
    )
    assert client.calls[0][0] == Hint.url


def test_official_adapter_falls_back_to_baseline_url_when_hint_fails() -> None:
    class Hint:
        source = "official"
        adapter = "neurips"
        route_kind = "index"
        url = "https://proceedings.neurips.cc/paper_files/paper/2025/"

    class Catalog:
        def verified_routes(self, *, venue):
            return [Hint()]

    class Client:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            if url == Hint.url:
                raise RuntimeError("stale route")
            return HttpResponse(url=url, final_url=url, status=200, headers={}, body=b"index")

    client = Client()
    adapter = NeurIPSAdapter(client, route_catalog=Catalog())
    baseline = "https://proceedings.neurips.cc/paper_files/paper/2025"
    adapter._get(baseline, spec=get_venue_spec("neurips"), route_kind="index")
    assert client.calls == [Hint.url, baseline]


def test_official_adapter_does_not_replace_aaai_archive_with_pagination_hint() -> None:
    class Hint:
        source = "official"
        adapter = "aaai_ojs"
        route_kind = "index"
        url = "https://ojs.aaai.org/index.php/AAAI/issue/archive?page=2"

    class Catalog:
        def verified_routes(self, *, venue):
            return [Hint()]

    class Client:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            return HttpResponse(url=url, final_url=url, status=200, headers={}, body=b"archive")

    client = Client()
    adapter = AAAIOJSAdapter(client, route_catalog=Catalog())
    baseline = AAAIOJSAdapter.ARCHIVE_URL
    adapter._get(baseline, spec=get_venue_spec("aaai"), route_kind="index")
    assert client.calls == [baseline]


def test_official_adapter_rejects_aaai_archive_subpath_hint() -> None:
    class Hint:
        source = "official"
        adapter = "aaai_ojs"
        route_kind = "index"
        url = "https://ojs.aaai.org/index.php/AAAI/issue/archive/garbage"

    class Catalog:
        def verified_routes(self, *, venue):
            return [Hint()]

    class Client:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            return HttpResponse(url=url, final_url=url, status=200, headers={}, body=b"archive")

    client = Client()
    adapter = AAAIOJSAdapter(client, route_catalog=Catalog())
    baseline = AAAIOJSAdapter.ARCHIVE_URL
    adapter._get(baseline, spec=get_venue_spec("aaai"), route_kind="index")
    assert client.calls == [baseline]


def test_headless_raw_persists_captured_route_without_second_fetch(tmp_path) -> None:
    from llm_security_digest.papers.headless import HeadlessDiscovery

    class BrowserResponse:
        status = 200
        url = "https://proceedings.neurips.cc/paper_files/paper/2025"
        headers = {"content-type": "text/html"}

        def body(self):
            return b"<html>index</html>"

    class Page:
        def goto(self, *_args, **_kwargs):
            return BrowserResponse()

    class Context:
        def route(self, *_args):
            return None

        def new_page(self):
            return Page()

        def close(self):
            return None

    class Browser:
        def new_context(self, **_kwargs):
            return Context()

        def close(self):
            return None

    class Playwright:
        class chromium:
            @staticmethod
            def launch(**_kwargs):
                return Browser()

    class Manager:
        def start(self):
            return Playwright()

        def stop(self):
            return None

    catalog = RouteCatalog(tmp_path)
    result = HeadlessDiscovery(playwright_factory=lambda: Manager()).collect_raw(
        {"urls": [BrowserResponse.url]},
        route_catalog=catalog,
        route_context={"venue": "neurips", "source": "official", "adapter": "neurips", "route_kind": "index"},
    )
    assert result["status"] == "ok"
    assert result["responses"][0]["route_catalog"]["verification_state"] == "verified"
    assert len(catalog.verified_routes(venue="neurips")) == 1
