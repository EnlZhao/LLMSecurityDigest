"""Bounded headless-browser discovery for registered paper sources.

This module is deliberately not a paper adapter.  Browser output is evidence
for candidate ranking or a raw response transport only; authoritative facts
still come from the regular Python source adapters and materializer.  No
browser page is allowed to write ``PaperFacts`` or ``facts.json``.
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urljoin, urlsplit

from .. import config
from .http import _secret_query_key
from .models import VENUE_SPECS


MAX_URLS = 10
MAX_TEXT_CHARS = 20_000
MAX_LINKS = 200
MAX_OUTPUT_BYTES = 512 * 1024
DEFAULT_TIMEOUT_MS = 20_000
MAX_REDIRECTS = 5
MAX_RAW_RESPONSE_BYTES = config.MAX_PDF_BYTES
MAX_RAW_OUTPUT_BYTES = 36 * 1024 * 1024
_SENSITIVE_RESPONSE_HEADERS = frozenset({"set-cookie", "set-cookie2"})


class HeadlessDiscoveryError(ValueError):
    pass


def _canonical_host(value: Any) -> str:
    """Return a DNS-normalized host without accepting IP literals."""
    host = str(value or "").strip().rstrip(".").casefold()
    if not host:
        raise HeadlessDiscoveryError("browser URL must include a hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        raise HeadlessDiscoveryError("browser host must be a registered DNS name")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise HeadlessDiscoveryError("browser host is malformed") from exc


def _registered_hosts() -> frozenset[str]:
    values: set[str] = set()
    for spec in VENUE_SPECS:
        for url in spec.official_urls:
            parsed = urlsplit(url)
            if parsed.scheme == "https" and parsed.hostname:
                values.add(_canonical_host(parsed.hostname))
    # Official adapters use these hosts even when the catalog URL is a
    # landing portal (for example ACL and PMLR volume pages).
    values.update({
        "aclanthology.org", "proceedings.mlr.press", "proceedings.neurips.cc",
        "ojs.aaai.org", "www.ijcai.org", "www.usenix.org",
        "www.ndss-symposium.org", "ieeexplore.ieee.org", "dl.acm.org",
        "www.sigsac.org", "api2.openreview.net", "api.openreview.net",
        "openreview.net", "arxiv.org", "export.arxiv.org",
        # Additional baseline-owned hosts used by the deterministic DOI,
        # Crossref, IEEE, and PMLR full-text routes.
        "api.crossref.org", "doi.org", "ieeexploreapi.ieee.org",
        "www.ieee-security.org", "raw.githubusercontent.com",
        "www.computer.org", "csdl-downloads.ieeecomputer.org",
        # Bing is discovery-only. Its snippets and links are evidence for
        # Hermes; no deterministic adapter treats Bing as a fact authority.
        "www.bing.com", "bing.com",
    })
    return frozenset(values)


ALLOWED_HOSTS = _registered_hosts()


def _normalized_allowed_hosts(allowed_hosts: Iterable[str] | None) -> frozenset[str]:
    values = ALLOWED_HOSTS if allowed_hosts is None else frozenset(
        _canonical_host(value) for value in allowed_hosts
    )
    if not values:
        raise HeadlessDiscoveryError("browser host allowlist must not be empty")
    unregistered = values - ALLOWED_HOSTS
    if unregistered:
        raise HeadlessDiscoveryError(
            f"browser hosts are not registered: {sorted(unregistered)}"
        )
    return values


def _contains_secret_query(value: str) -> bool:
    try:
        pairs = parse_qsl(urlsplit(value).query, keep_blank_values=True)
    except ValueError:
        return True
    for key, _item in pairs:
        if _secret_query_key(key):
            return True
    return False


def validate_browser_url(value: Any, *, allowed_hosts: Iterable[str] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HeadlessDiscoveryError("browser URL must be a non-empty string")
    raw = value.strip()
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        raise HeadlessDiscoveryError("browser URL contains control characters")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise HeadlessDiscoveryError("browser URL is malformed") from exc
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise HeadlessDiscoveryError("browser URL must use HTTPS")
    if parsed.username or parsed.password or port is not None:
        raise HeadlessDiscoveryError("browser URL cannot contain credentials or a port")
    host = _canonical_host(parsed.hostname)
    if host not in _normalized_allowed_hosts(allowed_hosts):
        raise HeadlessDiscoveryError(f"browser host is not allowlisted: {host}")
    if parsed.fragment:
        # Fragments are client-side state and can cause the browser to expose
        # an unrelated view.  Callers can submit the same URL without one.
        raise HeadlessDiscoveryError("browser URL fragments are not allowed")
    if _contains_secret_query(raw):
        raise HeadlessDiscoveryError("browser URL query contains a secret-like parameter")
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


def validate_raw_request(value: Any) -> dict[str, Any]:
    """Validate a request for raw response transport output.

    Raw responses are an optional diagnostic/artifact path.  The request is
    intentionally separate from the DOM evidence schema so existing callers
    cannot accidentally start writing response bytes to their evidence file.
    """
    if not isinstance(value, dict):
        raise HeadlessDiscoveryError("browser raw request must be an object")
    unknown = set(value) - {"urls", "max_bytes", "timeout_ms"}
    if unknown:
        raise HeadlessDiscoveryError(f"browser raw request fields are not allowed: {sorted(unknown)}")
    urls = value.get("urls")
    if not isinstance(urls, list) or not urls or len(urls) > MAX_URLS:
        raise HeadlessDiscoveryError(f"browser raw request urls must contain 1-{MAX_URLS} entries")
    normalized_urls = []
    seen: set[str] = set()
    for url in urls:
        normalized = validate_browser_url(url)
        if normalized not in seen:
            normalized_urls.append(normalized)
            seen.add(normalized)
    max_bytes = value.get("max_bytes", MAX_RAW_RESPONSE_BYTES)
    timeout_ms = value.get("timeout_ms", DEFAULT_TIMEOUT_MS)
    for name, number, lower, upper in (
        ("max_bytes", max_bytes, 1, MAX_RAW_RESPONSE_BYTES),
        ("timeout_ms", timeout_ms, 1000, 60_000),
    ):
        if not isinstance(number, int) or isinstance(number, bool) or not lower <= number <= upper:
            raise HeadlessDiscoveryError(f"browser raw request {name} is outside the safety bound")
    return {"urls": normalized_urls, "max_bytes": max_bytes, "timeout_ms": timeout_ms}


def _safe_link(value: Any, *, base_url: str) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme or parsed.netloc:
        candidate = value.strip()
    else:
        candidate = urljoin(base_url, value.strip())
    try:
        return validate_browser_url(candidate)
    except HeadlessDiscoveryError:
        return None


def _safe_response_headers(value: Any) -> dict[str, str]:
    """Keep response metadata useful while dropping cookie material."""
    headers = value if isinstance(value, dict) else {}
    result: dict[str, str] = {}
    for key, item in headers.items():
        lowered = str(key).casefold()
        if lowered in _SENSITIVE_RESPONSE_HEADERS:
            continue
        result[lowered] = str(item)[:2_000]
    return result


def _response_body(response: Any, *, max_bytes: int) -> bytes:
    body_method = getattr(response, "body", None)
    body = body_method() if callable(body_method) else body_method
    if not isinstance(body, (bytes, bytearray, memoryview)):
        raise HeadlessDiscoveryError("browser response body is not bytes")
    body = bytes(body)
    if len(body) > max_bytes:
        raise HeadlessDiscoveryError(f"browser response exceeds {max_bytes} bytes")
    return body


def _redirect_chain(response: Any, *, fallback_url: str) -> tuple[str, ...]:
    """Read Playwright's request redirect chain without trusting page text."""
    request = getattr(response, "request", None)
    values: list[str] = []
    seen: set[int] = set()
    while request is not None and id(request) not in seen:
        seen.add(id(request))
        value = getattr(request, "url", None)
        if isinstance(value, str) and value:
            values.append(value)
        request = getattr(request, "redirected_from", None)
    if not values:
        values.append(fallback_url)
    values.reverse()
    final = getattr(response, "url", None)
    if isinstance(final, str) and final and final != values[-1]:
        values.append(final)
    return tuple(values)


def _redirect_depth(request: Any) -> int:
    depth = 0
    seen: set[int] = set()
    while request is not None and id(request) not in seen:
        seen.add(id(request))
        depth += 1
        request = getattr(request, "redirected_from", None)
    return max(depth - 1, 0)


def _install_route_guard(context: Any, *, allowed_hosts: frozenset[str]) -> None:
    """Block non-HTTPS/non-registered requests before the browser sends them."""
    route_method = getattr(context, "route", None)
    if not callable(route_method):
        raise HeadlessDiscoveryError("browser context does not support request guards")

    def guard(route: Any, browser_request: Any) -> None:
        try:
            validate_browser_url(getattr(browser_request, "url", ""), allowed_hosts=allowed_hosts)
            if _redirect_depth(browser_request) > MAX_REDIRECTS:
                raise HeadlessDiscoveryError("browser redirect limit exceeded")
        except Exception:
            route.abort("blockedbyclient")
            return
        route.continue_()

    route_method("**/*", guard)


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

    def fetch_raw(
        self,
        url: str,
        *,
        max_bytes: int = MAX_RAW_RESPONSE_BYTES,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        allowed_hosts: Iterable[str] | None = None,
        headers: dict[str, str] | None = None,
        provenance_url: str | None = None,
    ):
        """Fetch one raw response through a guarded headless browser.

        The return value is an ``HttpResponse`` carrying the original bytes;
        no HTML/JSON/PDF parsing occurs here.  Callers must pass those bytes
        back through the normal deterministic source adapter.
        """
        # Import lazily to keep the baseline HTTP module independent of the
        # optional Playwright dependency and to avoid a module cycle.
        from .http import HttpRequestError, HttpResponse

        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= MAX_RAW_RESPONSE_BYTES:
            raise HeadlessDiscoveryError("browser response max_bytes is outside the safety bound")
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 1_000 <= timeout_ms <= 60_000:
            raise HeadlessDiscoveryError("browser response timeout_ms is outside the safety bound")
        allowed = _normalized_allowed_hosts(allowed_hosts)
        request_url = validate_browser_url(url, allowed_hosts=allowed)
        safe_provenance_url = provenance_url or request_url
        validate_browser_url(safe_provenance_url, allowed_hosts=allowed)
        if _contains_secret_query(safe_provenance_url):
            # The request URL itself is validated above.  This check also
            # protects a caller-provided provenance URL from being persisted.
            raise HeadlessDiscoveryError("browser provenance URL query contains a secret-like parameter")

        factory = self._factory()
        manager = factory()
        browser = None
        context = None
        try:
            playwright = manager.start()
            browser = playwright.chromium.launch(headless=True)
            # No storage state, cookies, proxy credentials, or caller secrets
            # are supplied to the browser context.  JavaScript remains enabled
            # because some public anti-bot portals require it before returning
            # the same raw document a normal client would receive.
            context = browser.new_context(
                java_script_enabled=True,
                service_workers="block",
            )
            _install_route_guard(context, allowed_hosts=allowed)
            page = context.new_page()
            if headers:
                safe_headers = {}
                for key, value in headers.items():
                    lowered = str(key).casefold()
                    if lowered in {"accept", "accept-language", "user-agent", "accept-encoding"}:
                        safe_headers[str(key)] = str(value)[:500]
                if safe_headers:
                    setter = getattr(page, "set_extra_http_headers", None)
                    if callable(setter):
                        setter(safe_headers)
            response = page.goto(request_url, wait_until="domcontentloaded", timeout=timeout_ms)
            if response is None:
                raise HeadlessDiscoveryError("browser navigation returned no response")
            status = int(getattr(response, "status", 0) or 0)
            final_url = str(getattr(response, "url", "") or getattr(page, "url", "") or request_url)
            validate_browser_url(final_url, allowed_hosts=allowed)
            redirects = _redirect_chain(response, fallback_url=request_url)
            if len(redirects) - 1 > MAX_REDIRECTS:
                raise HeadlessDiscoveryError("browser redirect limit exceeded")
            for redirect_url in redirects:
                validate_browser_url(redirect_url, allowed_hosts=allowed)
            response_headers = _safe_response_headers(getattr(response, "headers", {}))
            try:
                content_length = int(response_headers.get("content-length", "0") or 0)
            except ValueError:
                content_length = 0
            if content_length > max_bytes:
                raise HeadlessDiscoveryError(f"browser response exceeds {max_bytes} bytes")
            body = _response_body(response, max_bytes=max_bytes)
            fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            safe_url = safe_provenance_url
            if status >= 400:
                raise HttpRequestError(status, safe_url)
            digest = hashlib.sha256(body).hexdigest()
            provenance = {
                "source_url": safe_url,
                "requested_url": safe_url,
                "final_url": final_url,
                "redirect_chain": list(redirects),
                "http_status": status,
                "response_sha256": digest,
                "sha256": digest,
                "transport": "headless",
                "fetched_at": fetched_at,
                "content_type": response_headers.get("content-type"),
            }
            return HttpResponse(
                url=safe_url,
                status=status,
                headers=response_headers,
                body=body,
                final_url=final_url,
                transport="headless",
                redirect_chain=redirects,
                provenance=provenance,
            )
        finally:
            if context is not None:
                try:
                    context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            try:
                manager.stop()
            except Exception:
                pass

    def collect_raw(self, request: dict[str, Any]) -> dict[str, Any]:
        """Collect bounded raw response artifacts without materializing facts."""
        normalized = validate_raw_request(request)
        responses: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for url in normalized["urls"]:
            try:
                response = self.fetch_raw(
                    url,
                    max_bytes=normalized["max_bytes"],
                    timeout_ms=normalized["timeout_ms"],
                    allowed_hosts=ALLOWED_HOSTS,
                )
                responses.append({
                    "url": response.url,
                    "final_url": response.final_url or response.url,
                    "http_status": response.status,
                    "headers": response.headers,
                    "content_type": response.headers.get("content-type"),
                    "redirect_chain": list(response.redirect_chain),
                    "response_sha256": response.sha256,
                    "fetched_at": (response.provenance or {}).get("fetched_at"),
                    "transport": response.transport,
                    "body_base64": base64.b64encode(response.body).decode("ascii"),
                })
            except Exception as exc:
                errors.append({"url": url, "error_type": type(exc).__name__, "message": str(exc)[:300]})
        result = {
            "schema_version": 2,
            "status": "ok" if responses and not errors else "partial" if responses else "error",
            "responses": responses,
            "errors": errors,
            "allowlisted_hosts": sorted(ALLOWED_HOSTS),
            "facts_written": False,
            "materializer": "baseline_only",
        }
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_RAW_OUTPUT_BYTES:
            raise HeadlessDiscoveryError("raw browser responses exceed output bound")
        return result

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
            context = browser.new_context(
                java_script_enabled=True,
                service_workers="block",
            )
            if callable(getattr(context, "route", None)):
                _install_route_guard(context, allowed_hosts=ALLOWED_HOSTS)
            page = context.new_page()
            for url in normalized["urls"]:
                try:
                    response = page.goto(url, wait_until="domcontentloaded", timeout=normalized["timeout_ms"])
                    status = int(response.status) if response is not None else None
                    final_url = str(getattr(response, "url", "") or getattr(page, "url", "") or url)
                    validate_browser_url(final_url)
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


@dataclass
class HeadlessResponseTransport:
    """Optional ``HttpClient`` transport for blocked public HTTPS sources."""

    playwright_factory: Callable[[], Any] | None = None

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        max_bytes: int,
        timeout_ms: int,
        allowed_hosts: Iterable[str] | None,
        provenance_url: str | None = None,
    ):
        if allowed_hosts is None:
            raise HeadlessDiscoveryError("headless fallback requires an explicit registered host allowlist")
        discovery = HeadlessDiscovery(playwright_factory=self.playwright_factory)
        return discovery.fetch_raw(
            url,
            headers=headers,
            max_bytes=max_bytes,
            timeout_ms=timeout_ms,
            allowed_hosts=allowed_hosts,
            provenance_url=provenance_url,
        )


def run_request_file(input_path: Path, output_path: Path, *, raw: bool = False) -> dict[str, Any]:
    if output_path.name.casefold() == "facts.json":
        raise HeadlessDiscoveryError("headless output path cannot be facts.json")
    request = json.loads(input_path.read_text(encoding="utf-8"))
    result = HeadlessDiscovery().collect_raw(request) if raw else HeadlessDiscovery().collect(request)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
