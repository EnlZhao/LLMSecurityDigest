"""Template for an optimizer candidate.

How to use
----------

1. Rename this file to ``<descriptive_name>_vN.py`` where ``N`` is a
   monotonically increasing version number. ``N`` starts at ``1`` for
   each ``<descriptive_name>`` family.
2. Replace the header comment block below with the actual class-of-
   problem rationale and review metadata.
3. Implement the optimised adapter object. It MUST expose the same
   public interface as the baseline object it replaces — the selector
   in :mod:`llm_security_digest.optimizer` does not translate between
   different interfaces.
4. Add a ``CandidateInfo`` entry for this module in
   :data:`llm_security_digest.optimizer.OPTIMIZER_REGISTRY`.

What this template looks like
-----------------------------

Below is a minimal example showing the comment block the reviewer
expects. The implementation is a no-op pass-through — that is the
correct starting point before the real fix is added.
"""

from __future__ import annotations

# --- BEGIN REVIEW HEADER ---------------------------------------------
#
# Candidate:    <descriptive_name>_v1.py
# Replaces:     llm_security_digest.papers.<baseline_module>.<baseline_object>
# Triggered by: <date>, <paper_id or run-id>
# Class of problem:
#   <one paragraph describing the general failure mode the candidate
#    addresses, not the single paper that surfaced it>
# Expected metric:
#   <name>: <direction> by <minimum_delta>
# Authorised by: <human reviewer>
# Activated at:  <ISO date — leave blank until activation>
#
# --- END REVIEW HEADER -----------------------------------------------


class OptimisedAdapter:
    """Pass-through adapter. Replace the methods with the actual fix.

    The class name and method signatures must match the baseline
    object. The selector in ``optimizer/__init__.py`` binds this class
    to the same call sites the baseline object occupies.
    """

    def __init__(self, baseline):
        # Hold a reference to the baseline so the candidate can
        # delegate when its own logic decides the baseline is correct.
        self._baseline = baseline

    def collect(self, plan):
        # Default behaviour: delegate to baseline. Replace this with
        # the actual fix once the class-of-problem rationale is
        # reviewed and the expected metric is defined.
        return self._baseline.collect(plan)


__all__ = ["OptimisedAdapter"]