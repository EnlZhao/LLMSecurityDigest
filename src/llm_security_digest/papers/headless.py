"""Bounded headless-browser discovery for registered paper sources.

This module is deliberately not a paper adapter.  Browser output is evidence
for candidate ranking only; authoritative facts still come from the regular
Python source adapters and materializer.  No browser page is allowed to write
``PaperFacts`` or ``facts.json``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

from .. import config
from .models import VENUE_SPECS


MAX_URLS = 10
MAX_TEXT_CHARS = 20_000
MAX_LINKS = 200
MAX_OUTPUT_BYTES = 512 * 1024
DEFAULT_TIMEOUT_MS = 20_000


def _registered_hosts() -> frozenset[str]:
    values: set[str] = set()
    for spec in VENUE_SPECS:
        for url in spec.official_urls:
            parsed = urlsplit(url)
            if parsed.scheme == "https" and parsed.hostname:
                values.add(parsed.hostname.casefold())
    # Official adapters use these hosts even when the catalog URL is a
    # landing portal (for example ACL and PMLR volume pages).
    values.update({
        "aclanthology.org", "proceedings.mlr.press", "proceedings.neurips.cc",
        "ojs.aaai.org", "www.ijcai.org", "www.usenix.org",
        "www.ndss-symposium.org", "ieeexplore.ieee.org", "dl.acm.org",
        "www.sigsac.org", "api2.openreview.net", "api.openreview.net",
        "arxiv.org", "export.arxiv.org",
    })
    return frozenset(values)


ALLOWED_HOSTS = _registered_hosts()


class HeadlessDiscoveryError(ValueError):
    pass


def validate_browser_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HeadlessDiscoveryError("browser URL must be a non-empty string")
    raw = value.strip()
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise HeadlessDiscoveryError("browser URL must use HTTPS")
    if parsed.username or parsed.password or parsed.port is not None:
        raise HeadlessDiscoveryError("browser URL cannot contain credentials or a port")
    host = parsed.hostname.casefold()
    if host not in ALLOWED_HOSTS:
        raise HeadlessDiscoveryError(f"browser host is not allowlisted: {host}")
    if parsed.fragment:
        # Fragments are client-side state and can cause the browser to expose
        # an unrelated view.  Callers can submit the same URL without one.
        raise HeadlessDiscoveryError("browser URL fragments are not allowed")
    return raw


def validate_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HeadlessDiscoveryError("browser request must be an object")
    unknown = set(value) - {"urls", "max_chars", "max_links", "timeout_ms"}
    if unknown:
        raise HeadlessDiscoveryError(f"browser request fields are not allowed: {sorted(unknown)}")
    urls = value.get("urls")
    if not isinstance(urls, list) or not urls or len(urls) > MAX_URLS:
        raise HeadlessDiscoveryError(f"browser request urls must contain 1-{MAX_URLS} entries")
    normalized_urls = []
    seen = set()
    for url in urls:
        normalized = validate_browser_url(url)
        if normalized not in seen:
            normalized_urls.append(normalized)
            seen.add(normalized)
    max_chars = value.get("max_chars", MAX_TEXT_CHARS)
    max_links = value.get("max_links", MAX_LINKS)
    timeout_ms = value.get("timeout_ms", DEFAULT_TIMEOUT_MS)
    for name, number, lower, upper in (
        ("max_chars", max_chars, 1, MAX_TEXT_CHARS),
        ("max_links", max_links, 1, MAX_LINKS),
        ("timeout_ms", timeout_ms, 1000, 60_000),
    ):
        if not isinstance(number, int) or isinstance(number, bool) or not lower <= number <= upper:
            raise HeadlessDiscoveryError(f"browser request {name} is outside the safety bound")
    return {"urls": normalized_urls, "max_chars": max_chars, "max_links": max_links, "timeout_ms": timeout_ms}


def _safe_link(value: Any, *, base_url: str) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme or parsed.netloc:
        candidate = value.strip()
    else:
        from urllib.parse import urljoin

        candidate = urljoin(base_url, value.strip())
    try:
        return validate_browser_url(candidate)
    except HeadlessDiscoveryError:
        return None


@dataclass
class HeadlessDiscovery:
    """Run a bounded browser session with an injectable Playwright factory."""

    playwright_factory: Callable[[], Any] | None = None

    def _factory(self) -> Callable[[], Any]:
        if self.playwright_factory is not None:
            return self.playwright_factory
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise HeadlessDiscoveryError("Playwright is not installed; browser discovery is optional") from exc
        return sync_playwright

    def collect(self, request: dict[str, Any]) -> dict[str, Any]:
        normalized = validate_request(request)
        evidence: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        factory = self._factory()
        manager = factory()
        browser = None
        try:
            playwright = manager.start()
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(java_script_enabled=True)
            page = context.new_page()
            for url in normalized["urls"]:
                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=normalized["timeout_ms"])
                    status = int(response.status) if response is not None else None
                    page_title = str(page.title() or "")[:500]
                    body_text = str(page.locator("body").inner_text(timeout=normalized["timeout_ms"]) or "")
                    links: list[str] = []
                    for href in page.locator("a").evaluate_all("els => els.map(el => el.href)")[: normalized["max_links"] * 2]:
                        link = _safe_link(href, base_url=url)
                        if link and link not in links:
                            links.append(link)
                        if len(links) >= normalized["max_links"]:
                            break
                    evidence.append({
                        "url": url,
                        "http_status": status,
                        "page_title": page_title,
                        "text_excerpt": " ".join(body_text.split())[: normalized["max_chars"]],
                        "links": links,
                    })
                except Exception as exc:
                    errors.append({"url": url, "error_type": type(exc).__name__, "message": str(exc)[:300]})
            context.close()
        finally:
            if browser is not None:
                browser.close()
            try:
                manager.stop()
            except Exception:
                pass
        result = {
            "schema_version": 1,
            "status": "ok" if evidence and not errors else "partial" if evidence else "error",
            "evidence": evidence,
            "errors": errors,
            "allowlisted_hosts": sorted(ALLOWED_HOSTS),
            "facts_written": False,
            "materializer": "baseline_only",
        }
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise HeadlessDiscoveryError("browser evidence exceeds output bound")
        return result


def run_request_file(input_path: Path, output_path: Path) -> dict[str, Any]:
    request = json.loads(input_path.read_text(encoding="utf-8"))
    result = HeadlessDiscovery().collect(request)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
