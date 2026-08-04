"""Smoke tests for the optimizer selector.

These tests confirm the public surface of the selector is stable.
They do not exercise the candidates themselves — each candidate
brings its own tests.
"""

from __future__ import annotations

from llm_security_digest.optimizer import (
    CandidateInfo,
    OPTIMIZER_REGISTRY,
    resolve_adapter_set,
)


def test_registry_is_empty_by_default():
    assert OPTIMIZER_REGISTRY == {}


def test_resolve_baseline_returns_no_adapters():
    label, adapters = resolve_adapter_set("baseline", baseline_factory=lambda _: object())
    assert label == "baseline"
    assert adapters == []


def test_resolve_unknown_set_raises():
    try:
        resolve_adapter_set("does-not-exist", baseline_factory=lambda _: object())
    except ValueError as exc:
        assert "does-not-exist" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_resolve_patch_without_active_falls_back_to_baseline():
    label, adapters = resolve_adapter_set("patch", baseline_factory=lambda _: object())
    assert label == "baseline"
    assert adapters == []


def test_candidate_info_is_frozen():
    info = CandidateInfo(
        key="example_v1",
        module="llm_security_digest.optimizer.example_v1",
        replaces="papers.example.Baseline",
        class_of_problem="<test fixture>",
        expected_metric="<test fixture>",
        authoriser="<test fixture>",
    )
    try:
        info.key = "changed"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("CandidateInfo should be frozen")