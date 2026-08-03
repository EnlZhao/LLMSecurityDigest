"""Small, credential-safe wrapper around the official ``openreview-py`` clients.

The source adapter owns pagination and fact parsing, while this module owns
client construction.  Keeping construction here makes the production path
easy to fake in offline tests and prevents the HTTP collector from silently
falling back to an ad-hoc JSON endpoint.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable


OPENREVIEW_V2_BASEURL = "https://api2.openreview.net"
OPENREVIEW_V1_BASEURL = "https://api.openreview.net"


@dataclass(frozen=True)
class OpenReviewCredentials:
    """Credentials read from the process environment, never serialized."""

    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "OpenReviewCredentials":
        values = os.environ if environ is None else environ
        username = values.get("OPENREVIEW_USERNAME", "").strip() or None
        password = values.get("OPENREVIEW_PASSWORD", "") or None
        return cls(username=username, password=password)


def _default_v2_constructor() -> Callable[..., Any]:
    try:
        from openreview import api
    except ImportError as exc:  # pragma: no cover - packaging catches this
        raise RuntimeError("openreview-py is required for the OpenReview source") from exc
    return api.OpenReviewClient


def _default_v1_constructor() -> Callable[..., Any]:
    try:
        import openreview
    except ImportError as exc:  # pragma: no cover - packaging catches this
        raise RuntimeError("openreview-py is required for the OpenReview source") from exc
    return openreview.Client


class OpenReviewClientFactory:
    """Lazily build the official v2 and v1 clients.

    ``openreview-py`` authenticates during construction when credentials are
    supplied.  Lazy construction means a v2 challenge can be reported and
    the controlled v1 compatibility attempt can still be made.  The factory
    accepts constructor overrides solely for deterministic, network-free
    tests; production defaults always resolve to the package's official
    classes.
    """

    def __init__(
        self,
        *,
        credentials: OpenReviewCredentials | None = None,
        v2_constructor: Callable[..., Any] | None = None,
        v1_constructor: Callable[..., Any] | None = None,
    ) -> None:
        self.credentials = credentials or OpenReviewCredentials.from_env()
        self._constructors = {
            "v2": v2_constructor,
            "v1": v1_constructor,
        }
        self._clients: dict[str, Any] = {}
        self._construction_errors: dict[str, Exception] = {}

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "OpenReviewClientFactory":
        return cls(credentials=OpenReviewCredentials.from_env(environ))

    def get(self, version: str) -> Any:
        if version not in {"v2", "v1"}:
            raise ValueError(f"unsupported OpenReview client version: {version}")
        if version in self._clients:
            return self._clients[version]
        if version in self._construction_errors:
            raise self._construction_errors[version]
        constructor = self._constructors[version]
        try:
            if constructor is None:
                constructor = _default_v2_constructor() if version == "v2" else _default_v1_constructor()
            baseurl = OPENREVIEW_V2_BASEURL if version == "v2" else OPENREVIEW_V1_BASEURL
            client = constructor(
                baseurl=baseurl,
                username=self.credentials.username,
                password=self.credentials.password,
            )
        except Exception as exc:
            self._construction_errors[version] = exc
            raise
        self._clients[version] = client
        return client


def openreview_error_message(exc: Exception) -> str:
    """Return an exception message with known process secrets removed."""

    message = str(exc)
    for key in ("OPENREVIEW_USERNAME", "OPENREVIEW_PASSWORD"):
        secret = os.getenv(key, "")
        if secret:
            message = message.replace(secret, "<redacted>")
    return re.sub(
        r"([?&](?:password|username|token|secret|api[_-]?key)=)[^&\s]+",
        r"\1<redacted>",
        message,
        flags=re.IGNORECASE,
    )[:300]


def is_openreview_auth_error(exc: Exception) -> bool:
    """Classify auth/challenge failures without depending on private classes."""

    status = getattr(exc, "status_code", getattr(exc, "code", None))
    if status in {401, 403}:
        return True
    marker = f"{type(exc).__name__} {exc}".casefold()
    return any(
        token in marker
        for token in (
            "auth",
            "credential",
            "forbidden",
            "unauthorized",
            "challenge",
            "mfa",
            "login",
            "password",
        )
    )


__all__ = [
    "OPENREVIEW_V1_BASEURL",
    "OPENREVIEW_V2_BASEURL",
    "OpenReviewClientFactory",
    "OpenReviewCredentials",
    "is_openreview_auth_error",
    "openreview_error_message",
]
