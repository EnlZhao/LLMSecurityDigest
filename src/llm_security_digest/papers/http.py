from __future__ import annotations

import hashlib
import json
import ipaddress
import time
import zlib
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Iterable
from urllib import error, parse, request


def _safe_url(url: str, provenance_url: str | None = None) -> str:
    """Remove credentials from URLs used in exceptions and provenance."""
    value = provenance_url or url
    parsed = parse.urlsplit(value)
    if not parsed.query:
        return value
    safe_query = []
    for key, item in parse.parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.casefold()
        if any(marker in lowered for marker in ("key", "token", "secret", "password", "auth")):
            item = "<redacted>"
        safe_query.append(parse.quote_plus(key) + "=" + parse.quote_plus(item))
    return parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "&".join(safe_query), parsed.fragment))


def _query_has_secret(url: str) -> bool:
    try:
        pairs = parse.parse_qsl(parse.urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        return True
    markers = ("key", "api_key", "apikey", "access_token", "token", "secret", "password", "passwd", "authorization", "auth", "credential", "cookie", "session")
    for key, _ in pairs:
        lowered = key.casefold().replace("-", "_")
        if lowered in markers or lowered.endswith(("_api_key", "_access_token", "_token")):
            return True
        if any(lowered.startswith(marker + "_") for marker in markers if marker not in {"token", "auth"}):
            return True
        if lowered.startswith(("token_", "auth_")):
            return True
    return False


class HttpRequestError(RuntimeError):
    def __init__(self, code: int, url: str):
        self.code = code
        self.url = url
        super().__init__(f"HTTP {code} for {url}")


class HttpFallbackError(RuntimeError):
    """Both direct HTTP and the optional browser transport failed."""

    def __init__(self, direct: Exception, fallback: Exception, *, url: str):
        self.direct_error = direct
        self.fallback_error = fallback
        self.code = getattr(direct, "code", 0)
        self.url = _safe_url(url)
        direct_text = str(direct).replace(url, self.url)[:180]
        fallback_text = str(fallback).replace(url, self.url)[:180]
        super().__init__(f"direct request failed ({direct_text}); headless fallback failed ({fallback_text})")


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    final_url: str | None = None
    transport: str = "http"
    redirect_chain: tuple[str, ...] = ()
    provenance: dict[str, Any] | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)


def _hostname(url: str) -> str:
    parsed = parse.urlsplit(url)
    if parsed.scheme.casefold() != "https":
        raise ValueError("only HTTPS requests are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL credentials are not allowed")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL must include a hostname")
    # A baseline request should never be able to turn into a local-network
    # request through a literal IP. Registered public host names are checked
    # by the allowlist below; IP literals are rejected even when no allowlist
    # was supplied.
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
        raise ValueError("private or reserved IP hosts are not allowed")
    return hostname.casefold().rstrip(".")


def _normalize_allowed_host(value: object) -> str:
    """Normalize a host entry without accepting URL/userinfo surprises."""
    candidate = str(value or "").strip()
    if not candidate:
        raise ValueError("allowed host must not be empty")
    # Parse a host-only value as a URL so brackets/ports/userinfo cannot be
    # smuggled into the comparison. Registered callers pass bare DNS names.
    if "://" in candidate or "/" in candidate or "@" in candidate:
        raise ValueError("allowed host must be a bare hostname")
    try:
        parsed = parse.urlsplit(f"https://{candidate}")
        if parsed.port is not None or parsed.username is not None or parsed.password is not None:
            raise ValueError("allowed host must not include credentials or a port")
    except ValueError as exc:
        raise ValueError("allowed host is malformed") from exc
    return _hostname(f"https://{candidate}")


class _AllowedRedirectHandler(request.HTTPRedirectHandler):
    """Validate every redirect before urllib opens the next location."""

    def __init__(self, allowed_hosts: frozenset[str] | None):
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        hostname = _hostname(newurl)
        if self.allowed_hosts is not None and hostname not in self.allowed_hosts:
            raise ValueError(f"redirect host is not registered: {hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        retries: int = 3,
        timeout: int = 30,
        fallback_transport: Any | None = None,
        headless_transport: Any | None = None,
    ):
        if fallback_transport is not None and headless_transport is not None:
            raise ValueError("configure only one optional fallback transport")
        self.user_agent = user_agent
        self.retries = retries
        self.timeout = timeout
        self.fallback_transport = fallback_transport or headless_transport
        self._last_request: dict[str, float] = {}

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        min_interval: float = 0,
        max_bytes: int = 10 * 1024 * 1024,
        provenance_url: str | None = None,
        allowed_hosts: Iterable[str] | None = None,
    ) -> HttpResponse:
        allowed = None if allowed_hosts is None else frozenset(
            _normalize_allowed_host(item) for item in allowed_hosts
        )
        host = _hostname(url)
        if allowed is not None and host not in allowed:
            raise ValueError(f"request host is not registered: {host}")
        elapsed = time.monotonic() - self._last_request.get(host, 0)
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

        request_headers = {"User-Agent": self.user_agent, "Accept-Encoding": "identity"}
        request_headers.update(headers or {})
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                req = request.Request(url, headers=request_headers)
                self._last_request[host] = time.monotonic()
                if allowed is None:
                    # Keep the module-level opener for callers/tests that
                    # monkeypatch urllib.request.urlopen. Source fetches that
                    # need an allowlist use the guarded opener below.
                    response_context = request.urlopen(req, timeout=self.timeout)
                else:
                    opener = request.build_opener(_AllowedRedirectHandler(allowed))
                    response_context = opener.open(req, timeout=self.timeout)
                with response_context as response:
                    final_host = _hostname(response.geturl())
                    if allowed is not None and final_host not in allowed:
                        raise ValueError(f"response host is not registered: {final_host}")
                    body = response.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        raise ValueError(f"response exceeds {max_bytes} bytes")
                    response_headers = {key.lower(): value for key, value in response.headers.items()}
                    body = self._decode_body(body, response_headers, max_bytes=max_bytes)
                    return HttpResponse(
                        url=_safe_url(url, provenance_url),
                        status=int(getattr(response, "status", 200)),
                        headers=response_headers,
                        body=body,
                        final_url=_safe_url(response.geturl()),
                        transport="http",
                        provenance={
                            "source_url": _safe_url(url, provenance_url),
                            "final_url": _safe_url(response.geturl()),
                            "http_status": int(getattr(response, "status", 200)),
                            "response_sha256": hashlib.sha256(body).hexdigest(),
                            "transport": "http",
                        },
                    )
            except error.HTTPError as exc:
                # Do not let API credentials escape through a traceback or a
                # manifest message when a request fails.
                last_error = HttpRequestError(exc.code, _safe_url(url, provenance_url))
                if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= self.retries:
                    break
                wait = self._retry_after(exc.headers.get("Retry-After")) or (attempt + 1) * 3
                time.sleep(min(wait, 60))
            except (error.URLError, TimeoutError) as exc:
                # ``URLError.reason`` may include the complete request URL;
                # expose only a redacted, bounded diagnostic to callers.
                last_error = HttpRequestError(0, _safe_url(url, provenance_url))
                if attempt + 1 >= self.retries:
                    break
                time.sleep((attempt + 1) * 2)
        if last_error is None:
            raise RuntimeError("request failed")
        if (
            self.fallback_transport is not None
            and allowed is not None
            and last_error.code in {403, 408, 425, 429, 500, 502, 503, 504, 0}
            and _query_has_secret(url)
        ):
            raise HttpFallbackError(
                last_error,
                ValueError("headless fallback skipped: secret-like query parameter"),
                url=url,
            )
        if self._can_use_fallback(url, allowed=allowed, direct_error=last_error):
            try:
                response = self.fallback_transport.get(
                    url,
                    headers=request_headers,
                    max_bytes=max_bytes,
                    timeout_ms=max(1_000, min(int(self.timeout * 1_000), 60_000)),
                    allowed_hosts=allowed,
                    provenance_url=_safe_url(url, provenance_url),
                )
                if not isinstance(response, HttpResponse):
                    raise TypeError("headless fallback returned an invalid response")
                return response
            except Exception as fallback_error:
                raise HttpFallbackError(last_error, fallback_error, url=url) from fallback_error
        raise last_error

    def _can_use_fallback(
        self,
        url: str,
        *,
        allowed: frozenset[str] | None,
        direct_error: HttpRequestError,
    ) -> bool:
        """Only blocked registered requests may use the optional browser."""
        if self.fallback_transport is None or allowed is None:
            return False
        if _query_has_secret(url):
            return False
        if direct_error.code not in {403, 408, 425, 429, 500, 502, 503, 504, 0}:
            return False
        try:
            host = _hostname(url)
        except ValueError:
            return False
        return host in allowed

    @staticmethod
    def _decode_body(body: bytes, headers: dict[str, str], *, max_bytes: int) -> bytes:
        """Decode compressed HTTP bodies with a post-decompression bound.

        Some upstreams send gzip despite ``Accept-Encoding: identity`` and
        omit ``Content-Encoding``.  Magic-byte detection handles that case;
        streaming zlib output prevents a small gzip response from expanding
        past the configured response limit.
        """
        encoding = str(headers.get("content-encoding", "")).casefold()
        is_gzip = body.startswith(b"\x1f\x8b") or "gzip" in encoding
        if not is_gzip:
            return body
        if not body.startswith(b"\x1f\x8b"):
            raise ValueError("gzip content-encoding has an invalid body")
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            decoded = decompressor.decompress(body, max_bytes + 1)
            if len(decoded) > max_bytes or decompressor.unconsumed_tail:
                raise ValueError(f"decompressed response exceeds {max_bytes} bytes")
            tail = decompressor.flush(max_bytes + 1 - len(decoded))
            decoded += tail
        except zlib.error as exc:
            raise ValueError("invalid gzip response") from exc
        if len(decoded) > max_bytes or not decompressor.eof:
            raise ValueError(f"decompressed response exceeds {max_bytes} bytes")
        return decoded

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(float(value), 0)
        except ValueError:
            try:
                return max(parsedate_to_datetime(value).timestamp() - time.time(), 0)
            except Exception:
                return None
