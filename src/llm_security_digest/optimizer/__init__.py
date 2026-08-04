"""Optimizer layer for source-adapter fixes.

This subpackage holds **candidates** that replace or augment source
adapters in :mod:`llm_security_digest.papers` without modifying the
baseline. The baseline acquisition scripts in ``src/llm_security_digest/
papers/`` MUST NOT be modified to fix a single failing case. New
parsers, new URL patterns, retry policies, and fallback hosts belong
here, each as its own module.

Activation
----------

Activation is done by passing ``--adapter-set baseline|patch|<name>``
to ``run_daily.py``. The selector logic in this ``__init__`` resolves
the requested set and returns the module objects the pipeline should
use. ``baseline`` (the default) returns the unmodified objects from
``llm_security_digest.papers``; ``patch`` and named candidates return
the optimised objects registered below.

Writing a new candidate
-----------------------

1. Copy ``TEMPLATE.py`` to ``<descriptive_name>_vN.py``.
2. Fill the header comment with: which baseline line / function the
   candidate replaces, the triggering class of problem (not a single
   case), the expected metric, and the authorising human reviewer.
3. Implement the optimised adapter class with the same interface as
   the baseline object it replaces.
4. Register it in ``OPTIMIZER_REGISTRY`` below with a short key.
5. Run ``python -m pytest tests/test_optimizer.py`` to confirm the
   contract is preserved.

A candidate without a class-of-problem rationale is rejected on
review. A candidate that introduces a fact field is rejected by the
evolution validator. The two safety nets together keep the baseline
clean.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class CandidateInfo:
    """Metadata about an optimizer candidate."""

    key: str
    module: str
    replaces: str
    class_of_problem: str
    expected_metric: str
    authoriser: str
    activated_at: str | None = None


# Registry of available optimizer candidates. Each entry corresponds
# to a module in this subpackage that exposes the same interface as
# the baseline object it replaces. New candidates are added here when
# they pass review.
OPTIMIZER_REGISTRY: dict[str, CandidateInfo] = {}


def resolve_adapter_set(
    requested: str,
    *,
    baseline_factory: Callable[[str], Any],
) -> tuple[str, list[Any]]:
    """Resolve a ``--adapter-set`` value into the list of adapter objects.

    ``requested`` is one of:

    - ``"baseline"``: return the unmodified baseline objects.
    - ``"patch"``: alias for the most recently activated candidate.
    - any registered key in :data:`OPTIMIZER_REGISTRY`.

    ``baseline_factory`` is invoked with the requested key and is
    expected to return the baseline object the pipeline normally uses.
    The factory is invoked only when a candidate is selected so the
    baseline is not loaded unnecessarily.
    """

    if requested == "baseline":
        return "baseline", []

    if requested == "patch":
        # Resolve the patch alias to the latest activated candidate.
        latest: CandidateInfo | None = None
        for info in OPTIMIZER_REGISTRY.values():
            if info.activated_at and (latest is None or info.activated_at > info.activated_at):
                latest = info
        if latest is None:
            return "baseline", []
        requested = latest.key

    info = OPTIMIZER_REGISTRY.get(requested)
    if info is None:
        raise ValueError(
            f"unknown adapter set {requested!r}; "
            f"available: {sorted(OPTIMIZER_REGISTRY) or ['baseline']}"
        )

    import importlib

    module = importlib.import_module(info.module)
    return requested, [module]


__all__ = ["CandidateInfo", "OPTIMIZER_REGISTRY", "resolve_adapter_set"]