import json

import pytest

from llm_security_digest.papers.headless import HeadlessDiscovery, HeadlessDiscoveryError, validate_browser_url, validate_request


def test_headless_request_is_allowlisted_and_bounded():
    assert validate_browser_url("https://proceedings.mlr.press/v235/").startswith("https://")
    with pytest.raises(HeadlessDiscoveryError):
        validate_browser_url("http://proceedings.mlr.press/v235/")
    with pytest.raises(HeadlessDiscoveryError):
        validate_browser_url("https://evil.example/paper")
    with pytest.raises(HeadlessDiscoveryError):
        validate_request({"urls": ["https://proceedings.mlr.press/v235/"], "max_chars": 20001})


class _FakeLocator:
    def __init__(self, value):
        self.value = value

    def inner_text(self, **_kwargs):
        return self.value

    def evaluate_all(self, _script):
        return ["https://proceedings.mlr.press/v235/security.html", "https://evil.example/no"]


class _FakeResponse:
    status = 200


class _FakePage:
    def goto(self, *_args, **_kwargs):
        return _FakeResponse()

    def title(self):
        return "ICML Proceedings"

    def locator(self, selector):
        if selector == "body":
            return _FakeLocator("candidate evidence")
        return _FakeLocator("")


class _FakeContext:
    def new_page(self):
        return _FakePage()

    def close(self):
        pass


class _FakeBrowser:
    def new_context(self, **_kwargs):
        return _FakeContext()

    def close(self):
        pass


class _FakeChromium:
    def __init__(self):
        self.launches = 0

    def launch(self, **_kwargs):
        self.launches += 1
        return _FakeBrowser()


class _FakeManager:
    chromium = _FakeChromium()

    def start(self):
        return self

    def stop(self):
        pass


class _SeparatePlaywright:
    def __init__(self):
        self.chromium = _FakeChromium()


class _SeparatePlaywrightManager:
    def __init__(self):
        self.playwright = _SeparatePlaywright()
        self.stopped = False

    def start(self):
        return self.playwright

    def stop(self):
        self.stopped = True


def test_headless_output_is_evidence_only():
    result = HeadlessDiscovery(playwright_factory=lambda: _FakeManager()).collect({
        "urls": ["https://proceedings.mlr.press/v235/"],
        "max_chars": 100,
    })
    assert result["status"] == "ok"
    assert result["facts_written"] is False
    assert result["materializer"] == "baseline_only"
    assert result["evidence"][0]["links"] == ["https://proceedings.mlr.press/v235/security.html"]
    assert "PaperFacts" not in json.dumps(result)


def test_headless_uses_playwright_object_returned_by_start_and_stops_manager():
    manager = _SeparatePlaywrightManager()
    result = HeadlessDiscovery(playwright_factory=lambda: manager).collect({
        "urls": ["https://proceedings.mlr.press/v235/"],
    })
    assert result["status"] == "ok"
    assert manager.playwright.chromium.launches == 1
    assert manager.stopped is True
