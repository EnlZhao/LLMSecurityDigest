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
