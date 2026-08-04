# LLM Security Daily — Runbook

> The single source of truth for "what we do every day" and "what we do when
> something breaks". Read this end to end before touching the pipeline.
> When something fails, the fix goes here, not in a one-off note.

This document is owned by the daily pipeline itself. Every time a recurring
problem is observed and a generalisable fix is found, the corresponding
section is updated. Append-style "today I saw X" log entries are forbidden —
see §8 "Lessons Learned" for what belongs here instead.

## 0. Invariants

These are non-negotiable. They override any other guidance in this document
or in any overlay.

- **Fact fields are script-owned.** `title`, `authors`, `abstract`,
  `venue`, `publication_status`, `DOI`, `paper URLs`, `bibtex`, and
  `published_at` are written only by the Python acquisition pipeline.
  The LLM may select, summarise, and interpret, but it MUST NOT
  transcribe, paraphrase-into, translate-into, or repair any of these
  fields. If a field is missing in `facts.json`, the note shows one of
  `not_found / not_provided / ambiguous` — never a guess. This is the
  single most important rule of the project.
- **Top venues come first.** USENIX Security, NDSS, IEEE S&P, ACM CCS,
  IEEE TIFS, ACM TOSEM, plus ACL / EMNLP / ICML / NeurIPS / ICLR /
  AAAI / IJCAI for the language-model / safety tracks. arXiv is the
  broad discovery surface and the fallback; it is never the primary
  venue.
- **Pipeline order is fixed.** Top-venue registered proceedings →
  OpenReview accepted records → arXiv broad discovery reconciled to
  formal records → arXiv-only fill (only when formal slots are short)
  → LLM selection by `paper_id` → scripted download + BibTeX
  validation → SerpAPI scholar enrichment for shortlisted papers.
- **Self-evolution is generalisable.** A single failing case triggers
  a fix to the underlying class of problem, not a one-off patch. The
  `evolution/` overlay system exists exactly so a single paper cannot
  contaminate the pipeline.
- **Pushing is human-only.** The pipeline never `git push`. It writes
  artifacts under `RUN/` and under `.data/`. A human reviews, then
  pushes.

## 1. Daily Goal

Produce **ten** verified LLM-security papers per day under normal
conditions, each with a five-dimensional Chinese card. The "ten" is
the default; deviations are allowed only in two cases:

- **Pipeline-side failure**: when an authoritative source is down or
  a materialisation step rejects more papers than the candidates
  can absorb. The shortfall is reported in the manifest, not
  silently papered over.
- **Late-day exhaustion**: when the late hours produce fewer
  accepted papers than expected (e.g. a quiet Friday at a venue).
  The pipeline shortens the day honestly; it does not invent or
  pad.

Unverified papers are never published. "Verification" is binary:
every fact field present in `facts.json`, the full text downloaded,
and the BibTeX validated. There is no "mostly verified".

The default of ten may also be exceeded downward as the project's
traffic density shifts (e.g. mid-conference weeks vs off-season).
The pipeline reports the count truthfully; the user decides whether
a quiet day is acceptable.

### 1.1 Research direction

**Umbrella direction (stable):** LLM System Security — every
paper in this digest sits under this umbrella. The umbrella is
stable across phases; it does not change between months.

**Current main track (phase-dependent):** `Static Defense against
LLM Attackers` — environmental hardening, instruction isolation,
provenance gating, knowledge-source sanitisation, agent-loop
tripwires, decoy / honeypot content for attackers, and other
in-environment mitigations that obstruct LLM attackers from
completing their task. Active from 2026-08.

These two statements are the only place the research direction
is written. Both are updated only by the user. The LLM MUST NOT
modify them. Bucket assignment rules are in §6.1.

## 2. Daily Checklist

The pipeline runs roughly in this order. Each step has a single
verifiable outcome before moving to the next.

1. **Pre-flight**
   - Run `python scripts/llm_security/run_daily.py doctor`. Confirm
     all required sources respond and that the persistent data
     directory exists. If a source is down, decide: skip the day,
     or proceed without that source. The decision is recorded in
     §8.
   - Check `.data/evolution/active.json`. The active overlay is
     loaded automatically; verify the version matches the last
     successful day.

2. **Plan**
   - Run `python scripts/llm_security/run_daily.py init-plan --out
     RUN/<date>/search-plan.json`. The plan template encodes the
     venue priority order and the date window.
   - Edit only the **strategy** fields the LLM is allowed to
     touch: query keywords, venue groups, ranking hints, date
     window. Do not add paper-specific hints.

3. **Collect**
   - Run `python scripts/llm_security/run_daily.py collect --plan
     RUN/<date>/search-plan.json --out RUN/<date>/candidates.json`.
   - The script walks the registered top-venue adapters first,
     then OpenReview, then arXiv reconciliation. Network failures
     stay visible in the source report — do not silence them.

4. **Select**
   - Read `RUN/<date>/candidates.json`. Write
     `RUN/<date>/selection.json` with only `paper_id`, `score`,
     `category`, `reason`. Rank **more than ten** so verification
     can skip failures.
   - Categories come from §6. A paper belongs in one category
     only.

5. **Materialise**
   - Run `python scripts/llm_security/run_daily.py materialize
     --candidates … --selection … --facts RUN/<date>/facts.json
     --manifest RUN/<date>/manifest.json`.
   - The manifest reports `published / target`. If `published <
     target`, that is success-as-spec — fewer verified papers is
     fine.

6. **Read + analyse**
   - For each `paper_id` in `facts.json`, the LLM calls the
     `deeppapernote` skill, which in turn calls `outline`,
     `read-section`, and `find` from `run_daily.py`. The full PDF
     is never loaded into a single model request.
   - The skill writes one entry into `RUN/<date>/analysis.json`
     per paper with the bilingual abstract and the five
     dimensions.
   - The skill also writes a long-form Markdown note to
     `notes/<date>-<paper_id>.md` only when the user opts in
     (§10.2).

7. **Render**
   - Run `python scripts/llm_security/render_and_push.py --facts
     RUN/<date>/facts.json --manifest RUN/<date>/manifest.json
     --analysis RUN/<date>/analysis.json --date <date> --build-site`.
   - Inspect `docs/digest/<date>.html` and one or two paper cards
     before declaring the day done.

8. **Self-evolve**
   - Compare today's `published / target` and source report
     against §8 "Lessons Learned". If a new class of problem
     appeared, write an evolution candidate (see §7). If only an
     existing class recurred, update the entry count for that
     class.
   - If a quick non-overlay fix is required (e.g. a venue URL
     changed), apply it to the **optimizer** layer, never to
     `src/`. See §5.

9. **Push to user**
   - Push the digest URL plus a paper-id list to the user's daily
     channel. Feishu is the current default; the channel is
     configurable via `LLMSD_PUSH_TARGET`. The push happens at
     06:00 local time via the cron entry defined in §10.1.
   - The push MUST contain only what the previous steps
     produced. No invented summaries, no unverified papers.

## 3. Top-Venue URL Inventory

The actual URL table lives in a database, not in this document —
URLs rotate yearly and the script discovers them. The *shape* of
the table is fixed and lives here.

Each row records:

- `venue_key` (e.g. `usenix-security-2025`)
- canonical host (e.g. `www.usenix.org`)
- discovery path pattern (e.g. `/conference/usenixsecurity<year>/accepted-papers`)
- parser kind (`html_links` / `text` / `json`)
- last successful collection date
- last failed collection date and reason

The script treats this table as the only allowed source of venue
URLs. A venue not in the table is collected through OpenReview,
Crossref, or arXiv reconciliation — never by hard-coding a new URL
into the script.

When the table has a stale row (`last_success` > 90 days, or three
consecutive failures), §7 evolution kicks off a network search to
re-anchor the URL. See §8.

## 4. Acquisition Script Matrix

The acquisition layer is split by capability, not by source. Each
capability has a preferred path and a fallback path. Capabilities:

1. **Top-venue proceedings** — registered adapter in
   `src/llm_security_digest/papers/official.py`. The adapters
   cover USENIX, NDSS, ACL, EMNLP, PMLR (ICML), NeurIPS, AAAI,
   IJCAI, IEEE S&P. No key required. No login. **Always run
   first.**
2. **OpenReview accepted records** — covers ICLR, NeurIPS, ICML,
   UAI, and any other venue hosted on OpenReview. No login
   required for public notes and decision replies. The pipeline
   prefers the v2 API; v1 is a compatibility fallback.
3. **DOI / Crossref** — for IEEE TIFS, ACM CCS, ACM TOSEM, and
   any venue that registers with Crossref. No key required, but
   a `LLMSD_CONTACT_EMAIL` is set so Crossref's polite pool
   accepts us. **Primary path for ACM and IEEE journals.**
4. **arXiv reconciliation** — used after steps 1–3 to catch
   papers that were posted to arXiv but not yet indexed by the
   formal venue. The script reconciles by DOI, title, and author
   list; it does not promote an arXiv-only record to a venue
   without a matching formal source.
5. **IEEE Xplore API** — optional, only when
   `IEEE_XPLORE_API_KEY` is configured. Supplements Crossref for
   IEEE-published papers. Without a key, Crossref still covers
   the DOI metadata.
6. **SerpAPI / Scholar** — strictly enrichment. Queried only
   after a paper reaches the ranked shortlist. Never used to
   fill a fact field.
7. **GitHub Actions** — runs steps 1–3 in a clean network
   environment, used when the local Mac cannot reach a source.
   The action uploads a candidate artifact; the Mac downloads
   and materialises.
8. **OpenReview credentials** — used only when the public API
   refuses a venue request (rare). The username and password
   live in `.env` only; they are never committed.

When a capability fails, the failure is reported in
`manifest.json`'s `source_report`. The LLM must not invent a
workaround.

## 5. Script Optimisation Layer (Baseline Protection)

The acquisition scripts in `src/` are the **baseline**. They must
not be modified to fix a single failing case. Two extension points
exist:

- **`evolution/` overlay** — for strategy changes only: queries,
  keywords, ranking hints, prompt fragments. Already implemented
  in `src/llm_security_digest/evolution.py`.
- **`optimizer/` script candidates** — for source-adapter fixes:
  new parsers, new URL patterns, retry policies, fallback hosts.
  New candidates live under
  `src/llm_security_digest/optimizer/<name>_vN.py` and are
  activated by passing `--adapter-set baseline|patch|<name>` to
  `run_daily.py`.

Every optimizer candidate must carry a header comment listing:
which baseline line / function it replaces, the triggering class
of problem (not a single case), the expected metric, and the
authorising human. A candidate without a class-of-problem rationale
is rejected on review.

## 6. Paper Card Content Contract

Each published paper produces a `paper card` with these fields in this order.
The LLM is responsible for the complete bilingual abstract and four Chinese
interpretive dimensions. The Python pipeline is responsible for everything
else.

### 6.1 Main track and tags

A paper gets **one of two buckets** — `main_track` or `others`.
The bucket is derived from the **current main track** statement
in `DAILY.md` §1.1. There is no finer classification; the
previous 8-way category dictionary and the multi-axis tag
vocabulary were retired because the keyword classifier could not
keep them honest.

The current main track is set in `DAILY.md` §1.1 and is updated
only by the user. The LLM MUST NOT modify it.

The **only** tag we record is the venue axis (`venue:<short>`)
such as `venue:usenix-security-2025` or `venue:arxiv-preprint`.
The LLM does not invent venue tags; the Python pipeline fills
them from authoritative sources. A paper without a venue tag is
`not_provided`.

Selection priority is **`main_track` first**, then diversity in
`others`. The pipeline never silently over-promotes an `others`
paper to `main_track`; that decision is the user's.

### 6.2 Field table

| Field | Source | Required | Notes |
|---|---|---|---|
| `title` | `facts.json` | yes | Verbatim, never translated |
| `authors` | `facts.json` | yes | Verbatim list |
| `venue` | `facts.json` | yes | Canonical short name |
| `year` | `facts.json` | yes | |
| `doi` | `facts.json` | yes if assigned, else `not_provided` | |
| `bibtex` | `facts.json` | yes | |
| `summary_en` | `facts.json.abstract` | yes | Verbatim copy, no rewriting |
| `summary_zh` | LLM | yes | Complete academic Chinese translation of `facts.json.abstract`; no character cap |
| `problem_zh` | LLM | yes | Complete Chinese synthesis of the research problem, grounded in the translated abstract and paper sections |
| `contribution_zh` | LLM | yes | Complete Chinese synthesis of innovation/contribution, grounded in the translated abstract and paper sections |
| `method_zh` | LLM | yes | Complete Chinese synthesis of technical details, grounded in `sec:method` when available |
| `result_zh` | LLM | yes | Complete Chinese synthesis of experiment results, grounded in `sec:experiments` when available |

Every LLM-written dimension should include citations in the form
`(sec:<id>, p.<n>)` or `(sec:<id>)`. The renderer turns those into
links to the cached outline.

## 7. Self-Evolution Rules

Triggers (any one is enough):

- Same source fails three days in a row
- `published / target` drops below 0.6 for two consecutive days
- A new venue that has been requested ≥ 3 times is not in the URL
  table
- A user flags a recurring class of bad interpretation
- The four card dimensions are truncated or materially incomplete on ≥ 2 papers

Process (must follow this order):

1. **Reflect** — write a JSON candidate via `python
   scripts/llm_security/run_daily.py reflect --input
   candidate.json`. The candidate MUST include:
   - `root_cause`: a one-paragraph class-of-problem description
   - `generalisation`: why this fix covers more than the
     triggering case
   - `expected_metric`: name + direction + minimum delta
   - `positive_cases`: ≥ 1 trigger, ≥ 2 independent positive
     fixtures
   - `negative_cases`: ≥ 1 regression fixture
   - `trigger_paper_id`: the paper that surfaced the problem
2. **Validate** — `python scripts/llm_security/run_daily.py
   validate-evolution --version <id>`. Reject if any reflection
   field is missing or if the candidate contains a fact field.
3. **Shadow** — `python scripts/llm_security/run_daily.py
   shadow-evolution --version <id>`. Reads the persisted report
   under `.data/evolution/shadow/`.
4. **Activate** — `python scripts/llm_security/run_daily.py
   activate-evolution --version <id> --shadow-report
   <report-path>`. Atomic. Writes to `active.json`. The next day
   reads it automatically.

Rollback: `python scripts/llm_security/run_daily.py
rollback-evolution`. Always available; the last good active
version is restored.

What self-evolution may NOT do:

- Add a fact field to `facts.json`
- Bypass a failing source instead of reporting the failure
- Weaken the BibTeX / full-text validation
- Replace OpenReview with SerpAPI as an authority

## 8. Lessons Learned

Entries here describe a **class** of problem and a **general**
fix. Single-case observations without a class-of-problem
rationale do not belong here — they belong in
`RUN/<date>/reflection.json` and are consumed only by the next
evolution cycle.

(Entries will be appended here as the pipeline accumulates
experience. Each entry follows the template in §8.1.)

### 8.1 Lesson Entry Template

```markdown
### <short title>

- **Class of problem**: <what fails, in general — not "today's paper X">
- **Triggered by**: <date>, <paper_id or run-id>
- **Symptom**: <observable signal — log line, manifest field, user complaint>
- **Root cause**: <the underlying mechanism>
- **Fix**: <overlay / optimizer candidate / config change / doc change>
- **Expected metric**: <what improves and by how much>
- **Verified on**: <date>, <metrics>
```

### 8.2 Active Lessons

(Empty. Entries are appended as the pipeline accumulates
experience. The first real lesson replaces this paragraph.)

## 9. Push & Deep-Read Workflow

This is the part of the day that touches the user directly. Two
events matter.

### 9.1 Daily push at 06:00

- The cron job at 06:00 local time runs the full §2 checklist
  and triggers a Hermes notification. The notification contains:
  - the digest URL for today,
  - a bulleted list of the published `paper_id`s with their
    titles (titles are verbatim from `facts.json`),
  - a one-line source report (`published / target`, any failed
    sources).
- Push target: the user's home channel. Feishu is the current
  default; the channel is configurable via `LLMSD_PUSH_TARGET`.
- The push MUST NOT contain unverified papers, invented
  summaries, or any content not produced by the previous steps.

### 9.2 User-selected deep-read

- After reading the digest, the user replies with one or more
  `paper_id`s. The reply happens in the same channel as the
  push.
- For each selected `paper_id`, the LLM loads the
  `deeppapernote` skill and follows it end to end. The skill
  in turn calls `outline`, `read-section`, and `find` from
  `run_daily.py`.
- The skill writes one Markdown note per paper to
  `notes/<date>-<paper_id>.md`. The note's frontmatter carries
  only fact fields copied from `facts.json`; the body is the
  five dimensions plus a `## 我的批注` block.
- The note passes its grounding lint (no uncited claim, no fact
  field invented) before the skill declares success. A failing
  lint returns the paper to the user with an explicit error;
  it does not silently relax the rule.
- After the note passes, the skill uploads the file under
  `notes/` and pushes a one-line confirmation with the note
  URL back to the user's channel.
- Deep-read output is bound to the day's `paper_id`; it does
  not re-fetch fact fields and it does not trigger another
  acquisition cycle.

### 9.3 Why this is user-driven, not automatic

The acquisition pipeline produces facts; the deep-read step
produces human-quality interpretation. Interpretation is
expensive and the human reader knows which papers are worth the
cost. The pipeline never deep-reads all ten papers automatically
— that would burn budget on papers the user has no interest in
and would also encourage the LLM to write shallow notes for the
unselected papers.

## 10. References

- `AGENTS.md` — repository invariants; takes precedence on
  conflicts.
- `scripts/llm_security/hermes_prompt.md` — the LLM contract
  for the daily run.
- `scripts/llm_security/run_daily.py --help` — every
  subcommand.
- `~/.hermes/skills/deeppapernote/SKILL.md` — the per-paper
  deep-reading skill, used in step 6 of §2 and in §9.2.
- `~/.hermes/skills/deeppapernote/references/evidence-first.md`
  and `metadata-sources.md` — field-stable interpretive rules.
- `docs/DAILY.html` — auto-rendered mirror of this document;
  check the rendered output is sane after editing.
