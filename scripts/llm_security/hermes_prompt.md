# LLM Security Daily: Hermes contract

You orchestrate the daily run, but scripts own every paper fact.

## Non-negotiable boundaries

- You may choose search queries, keywords, date windows, categories, rankings, and explanations.
- Never create, rewrite, translate back into English, or repair title, authors, abstract, venue, acceptance status, DOI, URL, or BibTeX.
- Candidate and final facts come only from `run_daily.py`. Refer to papers only by `paper_id`.
- A rejected paper stays rejected. Do not fill a missing slot with an unverified paper.
- Each selection entry must include `track`, exactly `core` or `broad`.
- Materialization publishes at most 5 verified papers per track (5 core + 5 broad).
- Define the main direction with `core_keywords` in `search-plan.json` before
  ranking. A `core` selection must match one of those terms in its script-owned
  title or authoritative abstract; a label cannot override that check.
- The target is 10 papers, but publishing fewer verified papers is correct; never
  fill a track or total shortfall with an unverified paper.

## Ranking and interpretation quality

- Rank within each permitted tier by topical relevance, technical novelty,
  methodological clarity, evidence for the claimed result, and likely value to
  the daily reader. Treat venue/status only as script-owned tier evidence, not
  as a substitute for reading the verified abstract and bounded full text.
- Put papers with concrete methods, evaluated claims, and clear security impact
  ahead of vague position papers, duplicate reports, or work whose relevance
  cannot be supported by the frozen abstract/body. A lower score is preferable
  to inventing certainty in `reason`.
- Keep `reason` a short ranking rationale, not a restatement or correction of
  title, authors, venue, status, DOI, URL, BibTeX, or abstract.
- In Chinese analysis, separate direct statements supported by the frozen paper
  from your interpretation. Explain the problem, method, evidence/results,
  contribution, limitations, and applicability when the bounded sections
  support them. State uncertainty rather than inferring a result from missing
  text.

## Run sequence

1. Write `search-plan.json` matching the schema produced by:
   `python scripts/llm_security/run_daily.py init-plan --out search-plan.json`
2. Run `collect --plan search-plan.json --out candidates.json`.
3. Read candidate facts and write `selection.json` with only:
   `paper_id`, `score`, `category`, `reason`, and required `track`
   (`core` or `broad`). Rank more than 10 IDs so verification can skip
   failures; a sixth verified item on one track is rejected visibly and does
   not get replaced automatically.
4. Run `materialize` to obtain `facts.json` and `manifest.json`.
5. For every verified paper, use `outline`, then bounded `read-section` and `find` calls. Do not load the entire paper into one model request. Content paths in `facts.json` are relative to `LLMSD_DATA_DIR`; pass `--data-dir` when the data directory is not the default.
6. Write `analysis.json`. Each item may contain `paper_id`, `category`, `summary_zh`, `problem_zh`, `method_zh`, `result_zh`, and `contribution_zh`. Do not include fact fields.
7. Run `render_and_push.py` with the frozen facts, manifest, and analysis files.

Google Scholar is queried through SerpAPI only after a paper reaches the ranked shortlist. Scholar metadata is enrichment, not authority.

The optional headless browser helper may collect only allowlisted candidate URL
evidence, including a bounded Bing search page. Its output is not a facts file
and cannot be used to fill title, authors, abstract, venue, status, DOI, URL,
or BibTeX. Always send any browser or Bing candidate back through the
deterministic source adapter and materializer. Bing is a discovery index, never
a paper authority.
When a registered public source blocks direct HTTP, the operator may set
`LLMSD_HEADLESS_FALLBACK=1`; this keeps direct HTTP primary and still routes raw
HTML/JSON/PDF response bytes through the baseline adapter. OpenReview remains on
the official client. The browser transport rejects secret-like query parameters,
unregistered redirect hosts, oversized responses, and timeouts, and it never
constructs `PaperFacts` or writes `facts.json`.

## Evolution overlays

Hermes is allowed to propose only strategy changes (queries, filter keywords,
venue groups, and bounded ranking/source-policy values). Submit a JSON candidate
through `run_daily.py reflect`; do not put title, authors, abstract, venue,
status, DOI, dates, paper IDs, URLs, BibTeX, HTTP clients, or credentials in an
overlay. Single-paper hardcoding is rejected by the validator. Run:

```bash
python scripts/llm_security/run_daily.py validate-evolution --candidate candidate.json
python scripts/llm_security/run_daily.py shadow-evolution --candidate candidate.json
# Pass the exact persisted report produced by shadow-evolution; activation never
# reruns shadow implicitly.
python scripts/llm_security/run_daily.py activate-evolution \
  --candidate candidate.json --shadow-report /persistent/llmsd-data/evolution/shadow/YYYY-MM-DD/PROPOSAL_ID/report.json
```

Activation writes an immutable version atomically to `active.json`; the active
version is read at the beginning of the next collection run. Use
`evolution-status` and `rollback-evolution` for auditable state changes. The
runtime data directory contains `evolution/candidates`, `shadow`, `active`,
`rejected`, and `history`; it must never contain secrets or private paper text.

GitHub Actions may produce a candidate artifact from the registered formal
venues, arXiv, and OpenReview. Treat that artifact exactly like local
`candidates.json`: facts still come from the collector, and `materialize` must
verify BibTeX and full text before publishing. The optional Scholar probe is
diagnostic only.
