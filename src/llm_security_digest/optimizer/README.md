# Optimizer Layer

> Module-level fixes that augment the acquisition scripts in
> `src/llm_security_digest/papers/` **without** touching the baseline.

This package is the second of two extension points defined in
`DAILY.md` §5. The first is `evolution/`, which is for **strategy**
overlays (queries, keywords, ranking, prompt fragments). The
optimizer is for **source-adapter** changes (new parsers, new URL
patterns, retry policies, fallback hosts).

## Why the optimizer exists

The baseline acquisition scripts are validated, frozen, and audited.
A single failing paper must not cause a single line change in the
baseline. Instead, the fix goes here as a candidate, behind a name
and a review header. The selector in `optimizer/__init__.py` decides
at run time which candidate (if any) replaces the baseline.

## When to write a candidate

- A source URL pattern changed (a venue moved pages or restructured).
- A parser needs to handle a new HTML or PDF layout.
- A retry policy needs to back off differently for a flaky endpoint.
- A fallback host needs to be tried before the primary one gives up.

## When **not** to write a candidate

- The change is to a query, keyword, ranking hint, or prompt
  fragment. That belongs in `evolution/`.
- The change introduces a fact field, weakens validation, or
  replaces OpenReview / Crossref with SerpAPI. That is rejected by
  the validator and never reaches this package.

## How to write a candidate

1. Copy `TEMPLATE.py` to `<descriptive_name>_vN.py`.
2. Fill the header comment: what the candidate replaces, the
   triggering class of problem (not a single paper), the expected
   metric, the human reviewer who authorised it.
3. Implement the optimised adapter with the same public interface
   as the baseline object it replaces.
4. Register the candidate in `OPTIMIZER_REGISTRY` inside
   `optimizer/__init__.py`.
5. Run `python -m pytest tests/test_optimizer.py` to confirm the
   contract is preserved.
6. Activate with
   `python scripts/llm_security/run_daily.py … --adapter-set <name>`.

## Activation and rollback

Activation is recorded in the registry's `activated_at` field. The
selector treats the most recently activated candidate as the `patch`
alias. To roll back, clear the `activated_at` of the offending
candidate and re-run with `--adapter-set baseline`.

The registry is committed. The history of activations lives under
`.data/optimizer/` (gitignored) for forensic review.

## Relationship to `evolution/`

`evolution/` changes **what the pipeline searches for and how it
ranks results**. `optimizer/` changes **how the pipeline talks to a
source after a candidate has been chosen**. The two layers are
independent; both can be active on the same run.