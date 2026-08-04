import pytest

from llm_security_digest.evolution import EvolutionValidationError, apply_overlay, validate_overlay
from llm_security_digest.papers.models import SearchPlan


def test_core_keywords_add_is_validated_and_applied_additively() -> None:
    plan = SearchPlan(queries=["security"], core_keywords=["Prompt Injection"])
    overlay = {"search_plan": {"core_keywords_add": [" prompt injection ", "PROMPT INJECTION", " Backdoor "]}}

    validated = validate_overlay({"overlay": overlay})
    updated = apply_overlay(plan, validated["overlay"])

    assert updated.core_keywords == ["Prompt Injection", "Backdoor"]


@pytest.mark.parametrize(
    "values",
    ([""], ["x" * 101]),
)
def test_core_keywords_add_rejects_invalid_or_oversized_entries(values: list[str]) -> None:
    with pytest.raises(EvolutionValidationError, match="core_keywords_add"):
        validate_overlay({"overlay": {"search_plan": {"core_keywords_add": values}}})


@pytest.mark.parametrize(
    "sources",
    (
        ["arxiv"],
        ["official", "openreview", "arxiv"],
        ["openreview", "official", "crossref", "arxiv"],
        ["official", "openreview", "crossref", "ieee_xplore", "arxiv"],
    ),
)
def test_source_overlays_are_rejected(sources: list[str]) -> None:
    with pytest.raises(EvolutionValidationError, match="search_plan overlay fields are not allowed"):
        validate_overlay({"overlay": {"search_plan": {"sources": sources}}})


def test_apply_overlay_ignores_legacy_source_override() -> None:
    plan = SearchPlan(queries=["security"])
    updated = apply_overlay(plan, {"search_plan": {"sources": ["arxiv"]}})

    assert updated.sources == plan.sources


def test_target_cannot_be_changed_by_evolution_overlay() -> None:
    with pytest.raises(EvolutionValidationError, match="search_plan overlay fields are not allowed"):
        validate_overlay({"overlay": {"search_plan": {"target": 1}}})


def test_legacy_source_overlay_requires_explicit_read_compatibility() -> None:
    candidate = {"overlay": {"search_plan": {"sources": ["arxiv"]}}}

    with pytest.raises(EvolutionValidationError, match="search_plan overlay fields are not allowed"):
        validate_overlay(candidate)

    legacy = validate_overlay(candidate, allow_legacy_source_overlay=True)
    assert legacy["overlay"] == candidate["overlay"]
