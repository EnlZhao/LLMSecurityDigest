from __future__ import annotations

from dataclasses import dataclass

import pytest

from llm_security_digest.papers.models import SearchPlan
from llm_security_digest.papers.openreview_client import OpenReviewClientFactory, OpenReviewCredentials
from llm_security_digest.papers.sources import OpenReviewSource


VENUE_ID = "ICLR.cc/2025/Conference"


@dataclass
class FakeNote:
    id: str
    forum: str
    content: dict
    details: dict | None = None
    replyto: str | None = None
    invitations: list[str] | None = None
    pdate: int = 1750000000000
    mdate: int = 1750000000000

    def to_json(self):
        return {
            "id": self.id,
            "forum": self.forum,
            "content": self.content,
            "replyto": self.replyto,
            "invitations": self.invitations or [],
            "pdate": self.pdate,
            "mdate": self.mdate,
        }


def _root(note_id: str, title: str, *, accepted: bool = True) -> FakeNote:
    decision = FakeNote(
        id=f"{note_id}-decision",
        forum=note_id,
        replyto=note_id,
        invitations=[f"{VENUE_ID}/-/Decision"],
        content={"decision": "Accept (Poster)" if accepted else "Reject"},
    )
    return FakeNote(
        id=note_id,
        forum=note_id,
        content={
            "venueid": VENUE_ID,
            "venue": "ICLR 2025 Conference",
            "title": title,
            "abstract": f"Abstract for {title}",
            "authors": ["Ada Lovelace"],
        },
        details={"replies": [decision]},
    )


class PagingClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get_notes(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages.get(kwargs.get("offset", 0), [])


class FixedFactory:
    def __init__(self, v2, v1):
        self.clients = {"v2": v2, "v1": v1}
        self.versions = []

    def get(self, version):
        self.versions.append(version)
        value = self.clients[version]
        if isinstance(value, Exception):
            raise value
        return value


def _plan(max_results_per_venue: int = 2) -> SearchPlan:
    return SearchPlan(
        queries=["security"],
        sources=["openreview"],
        openreview_venues=[VENUE_ID],
        max_results_per_venue=max_results_per_venue,
        target=1,
    )


def test_factory_reads_headless_credentials_without_exposing_them():
    captured = []

    def constructor(**kwargs):
        captured.append(kwargs)
        return object()

    factory = OpenReviewClientFactory(
        credentials=OpenReviewCredentials("hermes@example.invalid", "secret-password"),
        v2_constructor=constructor,
        v1_constructor=constructor,
    )
    factory.get("v2")
    assert captured == [{
        "baseurl": "https://api2.openreview.net",
        "username": "hermes@example.invalid",
        "password": "secret-password",
    }]
    assert "secret-password" not in repr(factory.credentials)


def test_v2_structured_notes_are_paginated_and_parsed():
    first_page = [_root(f"p{index}", f"Paper {index}") for index in range(1000)]
    client = PagingClient({0: first_page, 1000: [_root("p1000", "Paper 1000")]})
    source = OpenReviewSource(FixedFactory(client, PagingClient({})))

    result = source.discover_result(_plan(1001))

    assert len(result.papers) == 1001
    assert [call["offset"] for call in client.calls] == [0, 1000]
    assert all(call["content"] == {"venueid": VENUE_ID} for call in client.calls)
    assert result.reports[0]["requests_succeeded"] == 2


def test_v2_challenge_falls_back_to_v1_but_remains_visible():
    class ChallengeError(Exception):
        pass

    v1 = PagingClient({0: [_root("legacy", "Legacy Paper")]})
    factory = FixedFactory(ChallengeError("challenge required"), v1)
    source = OpenReviewSource(factory)

    result = source.discover_result(_plan(1))

    assert [paper.source_id for paper in result.papers] == ["legacy"]
    assert factory.versions[:2] == ["v2", "v1"]
    errors = result.reports[0]["errors"]
    assert errors[0]["stage"] == "auth"
    assert errors[0]["error_type"] == "ChallengeError"


def test_v1_fallback_uses_invitation_filter():
    class V1OnlyClient(PagingClient):
        def get_notes(self, **kwargs):
            self.calls.append(kwargs)
            assert "content" not in kwargs
            assert kwargs["invitation"] == VENUE_ID
            return self.pages.get(kwargs.get("offset", 0), [])

    class V2Unavailable:
        def get_notes(self, **kwargs):
            raise RuntimeError("v2 unavailable")

    v1 = V1OnlyClient({0: [_root("legacy-v1", "Legacy V1 Paper")]})
    source = OpenReviewSource(FixedFactory(V2Unavailable(), v1))

    result = source.discover_result(_plan(1))

    assert [paper.source_id for paper in result.papers] == ["legacy-v1"]
    assert v1.calls[0]["invitation"] == VENUE_ID


def test_both_auth_failures_are_explicit_and_redacted(monkeypatch):
    monkeypatch.setenv("OPENREVIEW_PASSWORD", "secret-password")

    class AuthenticationError(Exception):
        pass

    factory = FixedFactory(
        AuthenticationError("password=secret-password"),
        AuthenticationError("password=secret-password"),
    )
    source = OpenReviewSource(factory)

    result = source.discover_result(_plan(1))

    assert result.papers == []
    assert result.reports[0]["status"] == "error"
    assert {error["stage"] for error in result.reports[0]["errors"]} == {"auth"}
    assert all("secret-password" not in str(error) for error in result.reports[0]["errors"])


def test_probe_uses_official_factory_and_reports_client_version():
    client = PagingClient({0: [_root("probe", "Probe Paper")]})
    factory = FixedFactory(client, PagingClient({}))
    source = OpenReviewSource(factory)

    report = source.probe(VENUE_ID)

    assert report["status"] == "ok"
    assert report["client_version"] == "v2"
    assert factory.versions == ["v2"]
