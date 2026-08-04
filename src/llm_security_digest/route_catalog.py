"""Persistent, baseline-owned catalog of verified venue routes.

The catalog stores route metadata only.  It is intentionally independent from
``PaperFacts`` and the materializer: a route is reusable only after its own
bounded HTTP verification succeeds.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .papers.http import HttpResponse, _secret_query_key
from .papers.models import VenueSpec, get_registered_venue_spec


DB_NAME = "route_catalog.sqlite3"
MAX_URL_CHARS = 4_096
MAX_METADATA_CHARS = 160
MAX_ERROR_CHARS = 400
MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class RouteCatalogError(ValueError):
    """A candidate cannot be admitted to the registered route catalog."""


@dataclass(frozen=True)
class RouteRecord:
    """A persisted route verification result."""

    id: int
    venue_key: str
    source: str
    adapter: str
    url: str
    route_kind: str
    verification_state: str
    http_status: int | None
    final_url: str | None
    redirect_chain: tuple[str, ...]
    response_hash: str | None
    first_verified_at: str | None
    last_verified_at: str | None
    evidence_source: str
    error_type: str | None
    error_message: str | None

    @property
    def verified(self) -> bool:
        return self.verification_state == "verified"

    @property
    def verification_status(self) -> str:
        return self.verification_state

    @property
    def first_verification_at(self) -> str | None:
        return self.first_verified_at

    @property
    def last_verification_at(self) -> str | None:
        return self.last_verified_at

    @property
    def response_sha256(self) -> str | None:
        """Compatibility name used by HTTP provenance payloads."""
        return self.response_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "venue_key": self.venue_key,
            "source": self.source,
            "adapter": self.adapter,
            "url": self.url,
            "route_kind": self.route_kind,
            "verification_state": self.verification_state,
            "http_status": self.http_status,
            "final_url": self.final_url,
            "redirect_chain": list(self.redirect_chain),
            "response_hash": self.response_hash,
            "response_sha256": self.response_hash,
            "first_verified_at": self.first_verified_at,
            "last_verified_at": self.last_verified_at,
            "evidence_source": self.evidence_source,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


# These are source/adapter host boundaries already used by the deterministic
# paper adapters.  Venue official URLs are added below for the venue itself.
_ADAPTER_HOSTS: dict[str, frozenset[str]] = {
    "acl_anthology": frozenset({"aclanthology.org"}),
    "aaai_ojs": frozenset({"ojs.aaai.org"}),
    "crossref": frozenset({
        "api.crossref.org", "doi.org", "dl.acm.org", "www.sigsac.org",
        "ieeexplore.ieee.org", "www.ieee-security.org", "www.computer.org",
    }),
    "cvf": frozenset({"openaccess.thecvf.com"}),
    "ecva": frozenset({"www.ecva.net"}),
    "eccv": frozenset({"www.ecva.net"}),
    "ieee_csdl": frozenset({
        "www.computer.org", "csdl-downloads.ieeecomputer.org",
    }),
    "ieee_xplore": frozenset({
        "ieeexplore.ieee.org", "ieeexploreapi.ieee.org",
    }),
    "ijcai": frozenset({"www.ijcai.org"}),
    "ndss": frozenset({"www.ndss-symposium.org"}),
    "neurips": frozenset({"proceedings.neurips.cc"}),
    "openreview": frozenset({"openreview.net", "api.openreview.net", "api2.openreview.net"}),
    "pmlr": frozenset({"proceedings.mlr.press", "raw.githubusercontent.com"}),
    "usenix": frozenset({"www.usenix.org"}),
}
_SOURCE_ALIASES = {
    "acl": "acl_anthology",
    "emnlp": "acl_anthology",
    "ieee": "ieee_csdl",
    "ieee-sp": "ieee_csdl",
    "xplore": "ieee_xplore",
    "cvpr": "cvf",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _metadata_text(value: Any, *, field: str, default: str | None = None) -> str:
    text = str(default if value is None else value).strip()
    if not text:
        if default is not None:
            return default
        raise RouteCatalogError(f"{field} must not be empty")
    if len(text) > MAX_METADATA_CHARS:
        raise RouteCatalogError(f"{field} exceeds {MAX_METADATA_CHARS} characters")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
        raise RouteCatalogError(f"{field} contains control characters")
    return text


def _canonical_host(hostname: str) -> str:
    host = str(hostname or "").strip().rstrip(".").casefold()
    if not host:
        raise RouteCatalogError("URL must include a hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        # Literal public addresses are unregistered too, while private and
        # reserved literals receive a more useful error for callers.
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise RouteCatalogError("private or reserved IP hosts are not allowed")
        raise RouteCatalogError("URL host must be a registered DNS name")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise RouteCatalogError("URL hostname is malformed") from exc


def _redact_url(value: Any) -> str:
    """Return a bounded URL safe for catalog metadata and errors."""
    if not isinstance(value, str):
        return "<invalid-url>"
    raw = value.strip()
    if len(raw) > MAX_URL_CHARS:
        raw = raw[:MAX_URL_CHARS]
    try:
        parsed = urlsplit(raw)
        host = parsed.hostname
        if not host:
            return "<invalid-url>"
        try:
            safe_host = _canonical_host(host)
        except RouteCatalogError:
            safe_host = "<invalid-host>"
        # Never persist credentials.  A secret-like query keeps its key but
        # loses the value, so diagnostics remain useful without leaking it.
        query_parts: list[str] = []
        try:
            pairs = parse_qsl(parsed.query, keep_blank_values=True)
        except ValueError:
            pairs = []
        for key, item in pairs:
            query_parts.append(f"{key}=<redacted>" if _secret_query_key(key) else f"{key}={item}")
        return urlunsplit((parsed.scheme.casefold(), safe_host, parsed.path, "&".join(query_parts), ""))[:MAX_URL_CHARS]
    except (TypeError, ValueError):
        return "<invalid-url>"


def _normalize_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RouteCatalogError("url must be a non-empty string")
    raw = value.strip()
    if len(raw) > MAX_URL_CHARS:
        raise RouteCatalogError("url exceeds the safety bound")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        raise RouteCatalogError("url contains control characters")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise RouteCatalogError("url is malformed") from exc
    if parsed.scheme.casefold() != "https":
        raise RouteCatalogError("only HTTPS routes are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise RouteCatalogError("URL credentials are not allowed")
    if port is not None:
        raise RouteCatalogError("URL ports are not allowed")
    if parsed.fragment:
        raise RouteCatalogError("URL fragments are not allowed")
    host = _canonical_host(parsed.hostname or "")
    try:
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError as exc:
        raise RouteCatalogError("URL query is malformed") from exc
    if any(_secret_query_key(key) for key, _item in query_pairs):
        raise RouteCatalogError("URL query contains a secret-like parameter")
    # Rebuild the netloc from the validated DNS host, eliminating any hidden
    # userinfo/port spelling and making route identity stable across case.
    return urlunsplit(("https", host, parsed.path, parsed.query, ""))


def _official_hosts(spec: VenueSpec) -> set[str]:
    hosts: set[str] = set()
    for value in spec.official_urls:
        try:
            parsed = urlsplit(value)
            if parsed.scheme.casefold() == "https" and parsed.hostname:
                hosts.add(_canonical_host(parsed.hostname))
        except (RouteCatalogError, ValueError):
            continue
    return hosts


def _registered_hosts(spec: VenueSpec, source: str) -> frozenset[str]:
    source_key = _SOURCE_ALIASES.get(source, source)
    allowed = _official_hosts(spec)
    adapter = str(spec.adapter or "").casefold()
    # ``official`` is a source kind, while the adapter owns the actual host.
    if source_key == "official":
        if "official" not in spec.source_kinds:
            return frozenset()
        source_key = adapter
    elif source_key not in spec.source_kinds and source_key != adapter:
        # arXiv is a cross-venue discovery source and is intentionally
        # available as a registered source even when not listed on VenueSpec.
        if source_key not in {"arxiv"}:
            return frozenset()
    if source_key == "arxiv":
        allowed.update({"arxiv.org", "export.arxiv.org"})
    else:
        allowed.update(_ADAPTER_HOSTS.get(source_key, ()))
    return frozenset(allowed)


def _safe_error(value: Any) -> str:
    message = str(value or "")
    # URLs in dependency errors are redacted before they enter metadata.
    message = re.sub(
        r"https?://[^\s)]+",
        lambda match: _redact_url(match.group(0)),
        message,
        flags=re.IGNORECASE,
    )
    message = re.sub(
        r"([?&](?:api[_-]?key|access[_-]?token|token|secret|password)=)[^&\s]+",
        r"\1<redacted>",
        message,
        flags=re.IGNORECASE,
    )
    return message[:MAX_ERROR_CHARS]


class RouteCatalog:
    """SQLite-backed catalog rooted strictly below a runtime data directory."""

    def __init__(self, data_dir: str | Path | None = None, *, db_name: str = DB_NAME):
        configured = data_dir or os.getenv("LLMSD_DATA_DIR") or (Path(__file__).resolve().parents[2] / ".data")
        root = Path(configured).expanduser().resolve()
        if not str(db_name) or Path(db_name).name != db_name or db_name in {".", ".."}:
            raise RouteCatalogError("catalog database name must be a plain filename")
        root.mkdir(parents=True, exist_ok=True)
        self.data_dir = root
        self.db_path = root / db_name
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS route_catalog (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venue_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    url TEXT NOT NULL,
                    route_kind TEXT NOT NULL,
                    verification_state TEXT NOT NULL,
                    http_status INTEGER,
                    final_url TEXT,
                    redirect_chain TEXT NOT NULL DEFAULT '[]',
                    response_hash TEXT,
                    first_verified_at TEXT,
                    last_verified_at TEXT,
                    evidence_source TEXT NOT NULL,
                    error_type TEXT,
                    error_message TEXT,
                    UNIQUE (venue_key, source, adapter, url, route_kind)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS route_catalog_venue_state "
                "ON route_catalog (venue_key, verification_state)"
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> RouteRecord:
        try:
            redirects = tuple(json.loads(row["redirect_chain"]))
        except (TypeError, ValueError):
            redirects = ()
        return RouteRecord(
            id=int(row["id"]),
            venue_key=str(row["venue_key"]),
            source=str(row["source"]),
            adapter=str(row["adapter"]),
            url=str(row["url"]),
            route_kind=str(row["route_kind"]),
            verification_state=str(row["verification_state"]),
            http_status=int(row["http_status"]) if row["http_status"] is not None else None,
            final_url=str(row["final_url"]) if row["final_url"] is not None else None,
            redirect_chain=redirects,
            response_hash=str(row["response_hash"]) if row["response_hash"] is not None else None,
            first_verified_at=str(row["first_verified_at"]) if row["first_verified_at"] is not None else None,
            last_verified_at=str(row["last_verified_at"]) if row["last_verified_at"] is not None else None,
            evidence_source=str(row["evidence_source"]),
            error_type=str(row["error_type"]) if row["error_type"] is not None else None,
            error_message=str(row["error_message"]) if row["error_message"] is not None else None,
        )

    def _save_attempt(
        self,
        *,
        venue_key: str,
        source: str,
        adapter: str,
        url: str,
        route_kind: str,
        verification_state: str,
        http_status: int | None,
        final_url: str | None,
        redirect_chain: Iterable[str],
        response_hash: str | None,
        evidence_source: str,
        error_type: str | None,
        error_message: str | None,
        verified_at: str | None,
    ) -> RouteRecord:
        redirects_json = json.dumps(list(redirect_chain), ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT first_verified_at, last_verified_at, response_hash "
                "FROM route_catalog WHERE venue_key=? AND source=? "
                "AND adapter=? AND url=? AND route_kind=?",
                (venue_key, source, adapter, url, route_kind),
            ).fetchone()
            first_verified_at = (
                str(existing["first_verified_at"])
                if existing is not None and existing["first_verified_at"] is not None
                else verified_at if verification_state == "verified" else None
            )
            # ``last_verified_at`` records the last verification attempt so a
            # failed refresh remains visible and auditable.  A route is still
            # reusable only when ``verification_state`` is ``verified``.
            last_verified_at = verified_at
            stored_response_hash = (
                response_hash
                if response_hash is not None
                else str(existing["response_hash"])
                if existing is not None and existing["response_hash"] is not None
                else None
            )
            connection.execute(
                """
                INSERT INTO route_catalog (
                    venue_key, source, adapter, url, route_kind,
                    verification_state, http_status, final_url, redirect_chain,
                    response_hash, first_verified_at, last_verified_at,
                    evidence_source, error_type, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (venue_key, source, adapter, url, route_kind) DO UPDATE SET
                    verification_state=excluded.verification_state,
                    http_status=excluded.http_status,
                    final_url=excluded.final_url,
                    redirect_chain=excluded.redirect_chain,
                    response_hash=excluded.response_hash,
                    first_verified_at=excluded.first_verified_at,
                    last_verified_at=excluded.last_verified_at,
                    evidence_source=excluded.evidence_source,
                    error_type=excluded.error_type,
                    error_message=excluded.error_message
                """,
                (
                    venue_key, source, adapter, url, route_kind,
                    verification_state, http_status, final_url, redirects_json,
                    stored_response_hash, first_verified_at, last_verified_at,
                    evidence_source, error_type, error_message,
                ),
            )
            row = connection.execute(
                "SELECT * FROM route_catalog WHERE venue_key=? AND source=? AND adapter=? "
                "AND url=? AND route_kind=?",
                (venue_key, source, adapter, url, route_kind),
            ).fetchone()
            assert row is not None
            return self._record(row)

    def verify(
        self,
        *,
        venue: str | VenueSpec,
        url: str,
        source: str = "official",
        adapter: str | None = None,
        route_kind: str = "landing",
        evidence_source: str = "cli",
        client: Any | None = None,
    ) -> RouteRecord:
        """Verify and persist one candidate route.

        Verification failures are returned as persisted non-verified records;
        callers can inspect the error without accidentally reusing the route.
        """
        checked_at = _utc_now()
        source_text = _metadata_text(source, field="source").casefold()
        adapter_text = _metadata_text(adapter if adapter is not None else source_text, field="adapter").casefold()
        route_kind_text = _metadata_text(route_kind, field="route_kind").casefold()
        evidence_text = _metadata_text(evidence_source, field="evidence_source")
        try:
            spec = get_registered_venue_spec(venue)
        except Exception:
            spec = None
        if spec is None:
            venue_key = _metadata_text(venue, field="venue").casefold()
            normalized_url = _redact_url(url)
            return self._save_attempt(
                venue_key=venue_key,
                source=source_text,
                adapter=adapter_text,
                url=normalized_url,
                route_kind=route_kind_text,
                verification_state="rejected",
                http_status=None,
                final_url=None,
                redirect_chain=(),
                response_hash=None,
                evidence_source=evidence_text,
                error_type="RouteCatalogError",
                error_message="venue is not registered",
                verified_at=checked_at,
            )
        venue_key = spec.key
        try:
            normalized_url = _normalize_url(url)
            allowed_hosts = _registered_hosts(spec, source_text)
            if not allowed_hosts:
                raise RouteCatalogError("source/adapter is not registered for venue")
            candidate_host = _canonical_host(urlsplit(normalized_url).hostname or "")
            if candidate_host not in allowed_hosts:
                raise RouteCatalogError(f"route host is not registered for venue: {candidate_host}")
        except Exception as exc:
            # Keep a deterministic key for a rejected URL, but never retain
            # credential material or secret query values.
            safe_url = _redact_url(url)
            return self._save_attempt(
                venue_key=venue_key,
                source=source_text,
                adapter=adapter_text,
                url=safe_url,
                route_kind=route_kind_text,
                verification_state="rejected",
                http_status=getattr(exc, "code", None) if isinstance(getattr(exc, "code", None), int) else None,
                final_url=None,
                redirect_chain=(),
                response_hash=None,
                evidence_source=evidence_text,
                error_type=type(exc).__name__,
                error_message=_safe_error(exc),
                verified_at=checked_at,
            )

        if client is None:
            # Import lazily so importing this metadata module never initializes
            # the full collector or optional browser dependency.
            from .papers.pipeline import default_client

            client = default_client()
        status: int | None = None
        response_hash: str | None = None
        try:
            response = client.get(
                normalized_url,
                allowed_hosts=allowed_hosts,
                max_bytes=MAX_RESPONSE_BYTES,
            )
            if not isinstance(response, HttpResponse):
                raise RouteCatalogError("HTTP client returned an invalid response")
            status = int(response.status)
            final_url = response.final_url or response.url or normalized_url
            final_url = _normalize_url(final_url)
            final_host = _canonical_host(urlsplit(final_url).hostname or "")
            if final_host not in allowed_hosts:
                raise RouteCatalogError(f"redirect host is not registered for venue: {final_host}")
            redirects = tuple(response.redirect_chain)
            if not redirects:
                redirects = (normalized_url,) if final_url == normalized_url else (normalized_url, final_url)
            normalized_redirects = tuple(_normalize_url(item) for item in redirects)
            if any(_canonical_host(urlsplit(item).hostname or "") not in allowed_hosts for item in normalized_redirects):
                raise RouteCatalogError("redirect host is not registered for venue")
            body = response.body
            if not isinstance(body, (bytes, bytearray, memoryview)):
                raise RouteCatalogError("HTTP response body is not bytes")
            response_hash = hashlib.sha256(bytes(body)).hexdigest()
            if status < 200 or status >= 400:
                raise RouteCatalogError(f"HTTP status {status} is not a successful route verification")
            return self._save_attempt(
                venue_key=venue_key,
                source=source_text,
                adapter=adapter_text,
                url=normalized_url,
                route_kind=route_kind_text,
                verification_state="verified",
                http_status=status,
                final_url=final_url,
                redirect_chain=normalized_redirects,
                response_hash=response_hash,
                evidence_source=evidence_text,
                error_type=None,
                error_message=None,
                verified_at=checked_at,
            )
        except Exception as exc:
            error_status = getattr(exc, "code", None)
            if isinstance(error_status, int):
                status = error_status
            return self._save_attempt(
                venue_key=venue_key,
                source=source_text,
                adapter=adapter_text,
                url=normalized_url,
                route_kind=route_kind_text,
                verification_state="failed",
                http_status=status,
                final_url=None,
                redirect_chain=(),
                response_hash=response_hash,
                evidence_source=evidence_text,
                error_type=type(exc).__name__,
                error_message=_safe_error(exc),
                verified_at=checked_at,
            )

    def list_routes(
        self,
        *,
        venue: str | VenueSpec | None = None,
        verified_only: bool = False,
    ) -> list[RouteRecord]:
        query = "SELECT * FROM route_catalog"
        values: list[Any] = []
        clauses: list[str] = []
        if venue is not None:
            spec = get_registered_venue_spec(venue)
            venue_key = spec.key if spec is not None else str(venue).strip().casefold()
            clauses.append("venue_key=?")
            values.append(venue_key)
        if verified_only:
            clauses.append("verification_state='verified'")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY venue_key, source, route_kind, id"
        with self._connect() as connection:
            return [self._record(row) for row in connection.execute(query, values).fetchall()]

    def verified_routes(self, *, venue: str | VenueSpec | None = None) -> list[RouteRecord]:
        """Read helper for adapters/index hints; never writes facts."""
        return self.list_routes(venue=venue, verified_only=True)

    # Keep the read/write names discoverable for small adapter integrations.
    verify_route = verify
    list = list_routes
    get_verified = verified_routes

    def reusable_route(
        self,
        *,
        venue: str | VenueSpec,
        url: str,
        source: str = "official",
        adapter: str | None = None,
        route_kind: str = "landing",
    ) -> RouteRecord | None:
        """Return a route only when a prior verification succeeded."""
        spec = get_registered_venue_spec(venue)
        venue_key = spec.key if spec is not None else str(venue).strip().casefold()
        try:
            normalized_url = _normalize_url(url)
        except RouteCatalogError:
            return None
        source_text = str(source).strip().casefold()
        adapter_text = str(adapter if adapter is not None else source_text).strip().casefold()
        route_kind_text = str(route_kind).strip().casefold()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM route_catalog WHERE venue_key=? AND source=? AND adapter=? "
                "AND url=? AND route_kind=? AND verification_state='verified'",
                (venue_key, source_text, adapter_text, normalized_url, route_kind_text),
            ).fetchone()
        return self._record(row) if row is not None else None


def verify_route(
    *,
    data_dir: str | Path | None = None,
    venue: str | VenueSpec,
    url: str,
    source: str = "official",
    adapter: str | None = None,
    route_kind: str = "landing",
    evidence_source: str = "cli",
    client: Any | None = None,
) -> RouteRecord:
    """Convenience wrapper around :class:`RouteCatalog.verify`."""
    return RouteCatalog(data_dir).verify(
        venue=venue,
        url=url,
        source=source,
        adapter=adapter,
        route_kind=route_kind,
        evidence_source=evidence_source,
        client=client,
    )


def list_routes(
    *,
    data_dir: str | Path | None = None,
    venue: str | VenueSpec | None = None,
    verified_only: bool = False,
) -> list[RouteRecord]:
    """Convenience reader for CLI/index integrations."""
    return RouteCatalog(data_dir).list_routes(venue=venue, verified_only=verified_only)


__all__ = [
    "DB_NAME",
    "RouteCatalog",
    "RouteCatalogError",
    "RouteRecord",
    "list_routes",
    "verify_route",
]
