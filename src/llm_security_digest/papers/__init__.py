"""Deterministic paper collection and verification."""

from .models import (
    PaperFacts,
    SearchPlan,
    SelectionEntry,
    VenueSpec,
    VENUE_REGISTRY,
    VENUE_SPECS,
    get_venue_spec,
)

__all__ = [
    "PaperFacts",
    "SearchPlan",
    "SelectionEntry",
    "VenueSpec",
    "VENUE_REGISTRY",
    "VENUE_SPECS",
    "get_venue_spec",
]
