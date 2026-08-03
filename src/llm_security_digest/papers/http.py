from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
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


class HttpRequestError(RuntimeError):
    def __init__(self, code: int, url: str):
        self.code = code
        self.url = url
        super().__init__(f"HTTP {code} for {url}")


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)


class HttpClient:
    def __init__(self, *, user_agent: str, retries: int = 3, timeout: int = 30):
        self.user_agent = user_agent
        self.retries = retries
        self.timeout = timeout
        self._last_request: dict[str, float] = {}

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        min_interval: float = 0,
        max_bytes: int = 10 * 1024 * 1024,
        provenance_url: str | None = None,
    ) -> HttpResponse:
        host = parse.urlparse(url).netloc
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
                with request.urlopen(req, timeout=self.timeout) as response:
                    body = response.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        raise ValueError(f"response exceeds {max_bytes} bytes")
                    return HttpResponse(
                        url=provenance_url or url,
                        status=int(getattr(response, "status", 200)),
                        headers={key.lower(): value for key, value in response.headers.items()},
                        body=body,
                    )
            except error.HTTPError as exc:
                # Do not let API credentials escape through a traceback or a
                # manifest message when a request fails.
                last_error = HttpRequestError(exc.code, _safe_url(url, provenance_url))
                if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= self.retries:
                    raise last_error from exc
                wait = self._retry_after(exc.headers.get("Retry-After")) or (attempt + 1) * 3
                time.sleep(min(wait, 60))
            except (error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt + 1 >= self.retries:
                    raise
                time.sleep((attempt + 1) * 2)
        raise last_error or RuntimeError("request failed")

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
