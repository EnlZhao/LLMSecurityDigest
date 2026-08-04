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
- The production target is fixed at 10 papers (5 `core` and 5 `broad`). The
  baseline may publish fewer only when authoritative refresh, BibTeX, full-text,
  or track validation rejects candidates; never lower the target in a plan or
  CLI argument, and never fill a shortfall with an unverified paper.

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
- Derive `category` from the paper's research content: use the title,
  authoritative abstract, and bounded full-text sections to name the research
  direction. Never use platform/source labels such as `arXiv`, `OpenReview`, a
  conference or journal name, or subject codes such as `cs.CR` as a category.
- In Chinese analysis, separate direct statements supported by the frozen paper
  from your interpretation. Explain the problem, method, evidence/results,
  contribution, limitations, and applicability when the bounded sections
  support them. State uncertainty rather than inferring a result from missing
  text. `summary_zh` must summarize the research question, method, and
  supported evidence/results; it must not describe the hosting or discovery
  platform instead of the research.

The rendered digest groups papers by research direction first. Within each
paper, mark the formal venue when one is verified, or label an unmatched paper
as an `arXiv` preprint, followed by its script-owned publication status.

## Run sequence

1. Write `search-plan.json` matching the schema produced by:
   `python scripts/llm_security/run_daily.py init-plan --out search-plan.json`
   Use only the exact baseline registry keys below. These are identifiers, not
   display names: `usenix-security`, `ieee-sp`, `acm-ccs`, `ndss`, `iclr`,
   `neurips`, `icml`, `cvpr`, `eccv`, `acl`, `emnlp`, `aaai`, `ijcai`,
   `tdsc`, `tifs`, and `tops` for `venue_groups`; use only `ieee-sp`,
   `acm-ccs`, `tdsc`, `tifs`, or `tops` for `crossref_venues`. For
   `openreview_venues`, use an exact registered cycle ID such as
   `ICLR.cc/2026/Conference`, `NeurIPS.cc/2026/Conference`, or
   `ICML.cc/2026/Conference` (the year may change, but the family and suffix
   must remain registered). Never send a display name, an invented group such
   as `top_security`, or a guessed OpenReview ID; omit an uncertain value and
   let the baseline report the missing source.
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

When browser/Bing discovery finds a usable annual proceedings or other official
index URL, persist it immediately after the baseline request succeeds:

```bash
python scripts/llm_security/run_daily.py route-catalog verify \
  --venue neurips \
  --url https://proceedings.neurips.cc/paper_files/paper/2025 \
  --source official --route-kind index --evidence-source hermes \
  --data-dir "$LLMSD_DATA_DIR"
```

The SQLite catalog is the durable hand-off between Hermes runs. It is stored at
`LLMSD_DATA_DIR/route_catalog.sqlite3` (and may be restored by the Actions
cache). Store one row per verified URL with the registered venue, source,
adapter, and `route_kind`; do not store the Bing URL or a link merely because a
snippet mentions it. Failed and rejected checks stay visible for diagnosis but
must never be treated as reusable. A later run may read only verified index
rows through the matching registered `OfficialAdapter`; the adapter applies
its URL grammar, year, and collection-scope checks before using a row. For
PMLR/ICML, a `/vNNN/` row is reusable only for that same volume and the root
index remains the fallback. OpenReview continues to use its official API
client and exact cycle ID; an OpenReview browser URL may be evidence, but is
not a route hint consumer. The official adapter still performs a fresh bounded
request and deterministic parse, so the catalog cannot become a facts cache.

The catalog identity is always the URL actually fetched. Any
`provenance_url` supplied to the browser is diagnostic metadata and cannot
relabel the response bytes or the persisted route.

For a headless request, pass the same registered venue context to
`headless_discover.py` so the captured response is persisted without a second
fetch. A route is reusable only when its catalog state is `verified`; the next
collection may use it as an index hint, but the official adapter still fetches,
parses, and validates the response. Never treat a catalog row as paper facts.

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
