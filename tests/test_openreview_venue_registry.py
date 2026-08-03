import pytest

from llm_security_digest.evolution import EvolutionValidationError, validate_overlay
from llm_security_digest.papers.models import (
    VENUE_SPECS,
    SearchPlan,
    get_registered_openreview_spec,
    get_registered_venue_spec,
    get_venue_spec,
)


REGISTERED_OPENREVIEW_IDS = tuple(
    venue_id
    for spec in VENUE_SPECS
    if "openreview" in spec.source_kinds
    for venue_id in spec.openreview_ids
)
UNREGISTERED_FAMILY_IDS = tuple(
    f"{venue_id.split('/')[0]}/2099/{venue_id.split('/')[2]}"
    for venue_id in REGISTERED_OPENREVIEW_IDS
)


def _plan(venue: str) -> SearchPlan:
    return SearchPlan(queries=["security"], openreview_venues=[venue])


def _overlay(venue: str) -> dict[str, object]:
    return {"overlay": {"search_plan": {"openreview_venues": [venue]}}}


def _source_request_overlay(venue: str) -> dict[str, object]:
    return {
        "overlay": {
            "source_requests": [{
                "venue_group": venue,
                "source_key": "openreview",
                "path": "/notes",
                "parser": "json",
            }],
        },
    }


@pytest.mark.parametrize("venue", REGISTERED_OPENREVIEW_IDS)
def test_search_plan_accepts_every_registered_openreview_id(venue: str) -> None:
    _plan(venue).validate()


@pytest.mark.parametrize(
    "venue",
    (" iclr.cc/2026/conference/ ", "ＩＣＬＲ.cc/２０２６/Ｃｏｎｆｅｒｅｎｃｅ/"),
)
def test_registered_openreview_ids_keep_unicode_and_casefold_normalization(venue: str) -> None:
    assert get_registered_openreview_spec(venue) is not None
    _plan(venue).validate()
    validate_overlay(_overlay(venue))
    validate_overlay(_source_request_overlay(venue))


@pytest.mark.parametrize("venue", UNREGISTERED_FAMILY_IDS)
def test_search_plan_rejects_unregistered_same_family_openreview_id(venue: str) -> None:
    with pytest.raises(ValueError, match="unknown OpenReview venue"):
        _plan(venue).validate()


@pytest.mark.parametrize("venue", (*UNREGISTERED_FAMILY_IDS, "Unknown.cc/2099/Conference"))
def test_search_plan_rejects_unregistered_openreview_id_as_venue_group(venue: str) -> None:
    with pytest.raises(ValueError, match="unknown venue group"):
        SearchPlan(queries=["security"], openreview_venues=[], venue_groups=[venue]).validate()


@pytest.mark.parametrize("venue", (*UNREGISTERED_FAMILY_IDS, "Unknown.cc/2099/Conference"))
def test_evolution_rejects_unregistered_openreview_id(venue: str) -> None:
    with pytest.raises(EvolutionValidationError, match="registered OpenReview venue ids"):
        validate_overlay(_overlay(venue))


@pytest.mark.parametrize("venue", (*UNREGISTERED_FAMILY_IDS, "Unknown.cc/2099/Conference"))
def test_evolution_source_requests_reject_unregistered_openreview_id(venue: str) -> None:
    with pytest.raises(EvolutionValidationError, match="venue_group is not registered"):
        validate_overlay(_source_request_overlay(venue))


def test_generic_venue_lookup_keeps_same_family_recognition_for_source_records() -> None:
    spec = get_venue_spec("ICLR.cc/2099/Conference")

    assert spec is not None
    assert spec.key == "iclr"
    assert get_registered_venue_spec("ICLR.cc/2099/Conference") is None
