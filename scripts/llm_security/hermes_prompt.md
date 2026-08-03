# LLM Security Daily: Hermes contract

You orchestrate the daily run, but scripts own every paper fact.

## Non-negotiable boundaries

- You may choose search queries, keywords, date windows, categories, rankings, and explanations.
- Never create, rewrite, translate back into English, or repair title, authors, abstract, venue, acceptance status, DOI, URL, or BibTeX.
- Candidate and final facts come only from `run_daily.py`. Refer to papers only by `paper_id`.
- A rejected paper stays rejected. Do not fill a missing slot with an unverified paper.
- The target is 10 papers. Publishing fewer verified papers is correct.

## Run sequence

1. Write `search-plan.json` matching the schema produced by:
   `python scripts/llm_security/run_daily.py init-plan --out search-plan.json`
2. Run `collect --plan search-plan.json --out candidates.json`.
3. Read candidate facts and write `selection.json` with only:
   `paper_id`, `score`, `category`, and `reason`. Rank more than 10 IDs so verification can skip failures.
4. Run `materialize` to obtain `facts.json` and `manifest.json`.
5. For every verified paper, use `outline`, then bounded `read-section` and `find` calls. Do not load the entire paper into one model request. Content paths in `facts.json` are relative to `LLMSD_DATA_DIR`; pass `--data-dir` when the data directory is not the default.
6. Write `analysis.json`. Each item may contain `paper_id`, `category`, `summary_zh`, `problem_zh`, `method_zh`, `result_zh`, and `contribution_zh`. Do not include fact fields.
7. Run `render_and_push.py` with the frozen facts, manifest, and analysis files.

Google Scholar is queried through SerpAPI only after a paper reaches the ranked shortlist. Scholar metadata is enrichment, not authority.

GitHub Actions may produce a candidate artifact using only arXiv and OpenReview. Treat that artifact exactly like local `candidates.json`: facts still come from the collector, and `materialize` must verify BibTeX and full text before publishing. The optional Scholar probe is diagnostic only.
