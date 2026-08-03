# Hermes Operations Contract

This document is the runbook for Hermes on the headless Linux collector. It
describes the baseline contract that must remain true when queries, prompts, or
skills evolve. Runtime state belongs under `LLMSD_DATA_DIR`; the repository
baseline is version controlled and is not an evolution workspace.

## Fixed daily order

The daily job always follows this order:

1. Collect registered formal venue records from the official adapters.
2. Collect OpenReview accepted records and arXiv metadata. Reconcile arXiv
   records with formal records before assigning a collection tier.
3. Rank only within the script-produced tiers. Unmatched arXiv records are a
   fallback pool and may be used only when the formal target is still short.
4. Ask the optional Scholar/SerpAPI workflow for enrichment of a small
   shortlist. Scholar cannot create or change facts.
5. Run `materialize`: fetch authoritative BibTeX and full text, verify title
   and paper identity, then freeze `facts.json`.
6. Read verified text in bounded sections (`outline`, `read-section`, and
   `find`) and write analysis by `paper_id`.
7. Render offline from the frozen facts, then write the daily reflection and
   propose an evolution candidate for a later run.

The target is a maximum, not a quota. Selection entries must include the
non-fact `track` value `core` or `broad`. Materialization publishes at
most five verified records per track (five core plus five broad); a sixth
verified record on a full track is rejected with a visible
`track_quota_exceeded` reason. A source failure, quota rejection, or
unverified paper must remain visible, and no shortfall is filled by an LLM
guess or an automatic substitute.

## Sources and adapters

`VenueSpec` in `src/llm_security_digest/papers/models.py` is the allowlisted
catalog. It contains the venue key, official host, OpenReview IDs, ISSNs, and
container titles. The formal tier is routed to deterministic adapters in
`papers/official.py` and `papers/sources.py`:

- USENIX Security and NDSS use their proceedings pages and citation metadata.
- ACL/EMNLP use ACL Anthology.
- ICML uses PMLR; NeurIPS uses the NeurIPS proceedings pages.
- CVPR uses CVF Open Access pages at `openaccess.thecvf.com`; it collects
  detail links from `CVPR<year>?day=all` and accepts only official detail/PDF
  URLs, citation metadata, DOI metadata, and official BibTeX links or inline
  BibTeX. ECCV uses its distinct ECVA archive at `www.ecva.net/papers.php`;
  the parser accepts only its year-bound detail and PDF URL grammars. It uses
  a page DOI for BibTeX content negotiation and otherwise remains incomplete.
- AAAI uses the OJS archive; IJCAI uses its proceedings pages.
- IEEE/ACM venue groups use registered Crossref queries with ISSN/container
  filtering, followed by DOI content negotiation for BibTeX. IEEE venues also
  have an optional `ieee_xplore` adapter backed by the official
  `ieeexploreapi.ieee.org` endpoint. It reads only `IEEE_XPLORE_API_KEY` from
  the environment, uses a baseline-owned publication registry, and emits an
  explicit `missing_api_key`, HTTP, or schema report when the API is not
  available. Crossref remains the independent authoritative path when the
  optional key is absent or the Xplore request fails; no LLM or snippet is
  used as a replacement.
- ACM has no unrestricted public search key in this collector. ACM CCS and
  TOPS metadata are obtained through the registered Crossref REST query. Once
  a DOI is verified, BibTeX is fetched by DOI content negotiation with
  `Accept: application/x-bibtex`; a Scholar or page snippet cannot populate
  ACM facts. Any future ACM endpoint must be added to the venue registry and
  its own key/host policy before it can run.
- OpenReview uses the official `openreview-py` API v2 client, with the
  controlled v1-compatible client as a fallback for older venues. Pagination
  is explicit (`limit`/`offset`), and structured `Note` objects plus decision
  replies are normalized by the baseline parser. The assigned venue and
  decision replies are checked; a loose string such as "accepted" in
  unrelated content is not sufficient. Optional headless credentials are
  read only from `OPENREVIEW_USERNAME` and `OPENREVIEW_PASSWORD`; anonymous
  access is attempted when they are absent. Login, MFA, challenge, and
  authorization failures remain visible in the source report with their
  request stage and error type.
- arXiv uses the Atom API and official BibTeX endpoint. `journal_ref` and
  comments are evidence only until a formal record is matched.

### Authoritative API and key boundaries

The adapters use the following public contracts. These are deterministic HTTP
inputs to the baseline code; an LLM never converts a response into metadata.

| Source | Baseline request and parsing contract | Credential / pacing |
| --- | --- | --- |
| OpenReview | `openreview-py` API v2 uses `https://api2.openreview.net` and paginated `get_notes` calls with `content`, `limit`, `offset`, and `details=replies`. The adapter follows the submission forum, assigned venue ID, and decision replies before accepting a record. For older venue deployments it uses the v1-compatible `https://api.openreview.net` client and the same venue/decision checks. | No API key is required for public notes. Optional `OPENREVIEW_USERNAME` and `OPENREVIEW_PASSWORD` are used only by the official client factory; auth/challenge failures remain in the report. |
| arXiv | Atom queries use `https://export.arxiv.org/api/query` with bounded `search_query`, `start`, and `max_results` parameters. The parser reads title, authors, summary, categories, dates, DOI, journal reference, comments, and links. BibTeX is fetched from `https://arxiv.org/bibtex/<id>` after the ID is normalized. | No key. Requests are serialized with the arXiv-recommended three-second minimum interval and bounded retries; a `429` or transport error is reported rather than replaced with a guess. |
| Crossref / DOI | Venue/ISSN queries use `https://api.crossref.org/works` and registered container filters. A DOI identity refresh uses `https://api.crossref.org/works/<doi>`. BibTeX is obtained by DOI content negotiation at `https://doi.org/<doi>` with `Accept: application/x-bibtex`, then checked against the title and every author. | No key. `LLMSD_CONTACT_EMAIL` is optional but recommended in the User-Agent/mailto for responsible rate limiting. |
| IEEE Xplore | Optional official endpoint `https://ieeexploreapi.ieee.org/api/v1/search/articles` with the registry-provided publication title, query text, pagination, and `format=json`. The API key is sent only as the `apikey` request parameter; provenance URLs redact it. | `IEEE_XPLORE_API_KEY` is required for this adapter. A missing key, HTTP error, or schema error is visible. Registered Crossref remains a separate authoritative path, not an LLM fallback. |
| ACM | There is no unregistered ACM private endpoint in the baseline. CCS/TOPS use the registered Crossref container/ISSN query and DOI content negotiation. ACM pages or Scholar snippets cannot supply facts. | No ACM key is needed for the Crossref path. Any future ACM API must first be added to the venue/host/key registry. |

The no-key formal sources (official proceedings, OpenReview public notes,
arXiv, and Crossref) run before optional IEEE and Scholar enrichment. API keys
are never written to source reports, candidate artifacts, evolution overlays,
or logs. The collector does not silently switch sources when one endpoint is
unavailable: the failed stage, HTTP status, and error type remain inspectable.

### External implementation review

The baseline design was checked against public implementations, but none is
installed as an unreviewed fact provider. The useful patterns are:

- [`daily-paper-reader`](https://github.com/ziwenhahaha/daily-paper-reader)
  keeps per-source fetchers under `src/maintain/fetchers/`. Its OpenReview
  fetcher uses both official client generations and accepts a decision only
  when the reply invitation ends in `/-/Decision`; its ACL, AAAI OJS, and
  IJCAI fetchers follow the official list/detail pages. We retain that source
  separation and invitation rule, while adding immutable provenance and
  required-field validation before a record becomes a fact.
- [`pranftw/openreview_scraper`](https://github.com/pranftw/openreview_scraper)
  demonstrates venue/year grouping and OpenReview extraction ergonomics, but
  its README asks the operator to put OpenReview credentials in `config.py`.
  It is therefore not a production dependency here. Its filtering and CSV
  extraction are useful patterns, but a generic `only_accepted` filter is not
  sufficient for this project; the baseline still requires an assigned venue
  plus an explicit decision reply or the narrowly defined final-venue
  compatibility evidence.
- [`Mahdisadjadi/arxivscraper`](https://github.com/Mahdisadjadi/arxivscraper)
  and the official arXiv Atom/BibTeX endpoints provide a no-key discovery
  path. The baseline uses the protocol, adds the official BibTeX identity
  check, and never treats `journal_ref` alone as publication evidence.
- [`tamnd/neurips-cli`](https://github.com/tamnd/neurips-cli) confirms that
  is a useful reference for the official-host boundary, but its current
  README explicitly calls the project a fresh scaffold whose real commands
  still need to be built. It is not used as a fact provider. The deterministic
  adapter uses the registered official proceedings host and parses HTML by
  code, never by an LLM.
- `paper-search-mcp` is a discovery/reference pattern only. It may inform
  where a deterministic adapter should look, but it cannot provide or correct
  paper facts. CVPR facts come only from CVF Open Access and ECCV facts only
  from ECVA responses that pass the baseline parser and validation.
- [`sophia-jihye/IEEE_Xplore_API_Python`](https://github.com/sophia-jihye/IEEE_Xplore_API_Python)
  is an API usage example, not an authority by itself. IEEE Xplore requires
  `IEEE_XPLORE_API_KEY`; the adapter is optional and emits an explicit auth
  report. Registered Crossref/DOI collection remains the no-key formal path.
- GitHub ACM search results include [`tamnd/acmdl-cli`](https://github.com/tamnd/acmdl-cli),
  which is also an explicit fresh scaffold, and browser scrapers such as
  [`smadaminov/paper-scraper`](https://github.com/smadaminov/paper-scraper),
  which require the operator's lawful digital-library access. They are not
  suitable anonymous fact sources. ACM CCS/TOPS therefore stay on the
  registered Crossref/DOI path until a reviewed public ACM endpoint exists.

The baseline uses anonymous requests for public proceedings, ACL, arXiv, and
Crossref. It does not install or execute an unreviewed GitHub scraper and does
not ask Hermes to log in to a publisher. Only the optional OpenReview
credentials, IEEE Xplore key, and Scholar SerpAPI key are secret-backed, and
each is scoped to the single request step that needs it. ACM has no equivalent
unrestricted public search endpoint in this baseline; ACM facts come from the
registered Crossref/DOI path until an independently reviewed official adapter
is added.

### Secret storage and threat model

GitHub Actions secrets must be configured in the repository or environment
Secrets UI (`SERPAPI_API_KEY` and, only if needed, `IEEE_XPLORE_API_KEY`,
`OPENREVIEW_USERNAME`, and `OPENREVIEW_PASSWORD`). They are injected only into
the single request step that needs them. Do not put a secret in workflow YAML,
an artifact, a candidate file, an evolution directory, a provenance URL, or a
debug log. The `workflow_run` Scholar job receives only `contents: read` and
`actions: read`; the latter is needed to download the completed candidate
artifact. That artifact is untrusted JSON and is parsed as data, never
executed or used to write facts.

On the persistent Linux host, run Hermes as a dedicated unprivileged service
user. Prefer a systemd credential or an external secret manager (Vault, cloud
secret manager, or an equivalent) and pass the value to the collector through
the process environment or an in-memory file descriptor. If a file is
necessary, keep it outside the repository and `chmod 600` it so only the
service user can read it. `.env` is a local-development convenience only; it
must remain untracked and should be replaced by the host secret manager in
production. Rotate a key if it has ever appeared in shell history, a command
line, CI output, an artifact, or a log.

Encryption at rest does not make a secret invisible to a fully compromised
machine: the running collector must be able to decrypt and use the value, so a
root attacker, administrator, debugger, malicious dependency, or equivalent
process can still capture it. External secret managers and short-lived scoped
tokens reduce static-file and artifact exposure, but cannot defeat a complete
runtime compromise. The baseline therefore limits where secrets are read and
redacts them from errors; it does not claim an impossible absolute guarantee.

Install the pinned OpenReview client through the package dependencies on the
headless host:

```bash
python -m pip install 'openreview-py>=1.46,<2'
```

The baseline factory calls the official client methods (the v2 constructor is
`openreview.api.OpenReviewClient`, the compatibility constructor is
`openreview.Client`) and passes only the registered base URL and optional
credentials. A representative bounded page is equivalent to:

```python
client = api.OpenReviewClient(baseurl="https://api2.openreview.net")
notes = client.get_notes(
    content={"venueid": "ICLR.cc/2025/Conference"},
    limit=100,
    offset=0,
    details="replies",
)
```

The adapter advances `offset` until its per-venue budget is reached, joins
forum replies in bounded pages, and classifies only an assigned venue plus an
explicit decision. It never treats an unstructured page label as acceptance.

Every adapter returns a structured report. At minimum inspect `status`,
`error_type`, `requests_attempted`, `requests_succeeded`, `records_scanned`,
`records_valid`, `records_filtered`, and `records_incomplete`. A non-zero
request failure is an operational signal, not permission to switch to an LLM
or a Scholar snippet.

### Evolution source requests

An evolution overlay may request an experiment against an existing source, but
it cannot add a host or a Python adapter. Each `source_request` must contain a
registered `venue_group`, the registered `source_key`, a URL path beginning
with `/`, and one parser enum: `text`, `json`, or `html_links` (an optional
bounded `max_bytes` is allowed). Absolute URLs, `//host` paths, schemes,
filesystem paths, traversal segments, unknown sources, single-paper IDs/DOIs,
detail-page paths, and arbitrary parser code are rejected. Only registered
collection/list paths (for example an ACL year index, a PMLR volume index, or
the Crossref `/works` collection) may be requested.

The baseline `BaselineHttpBroker` resolves the HTTPS host from the registry,
strips sensitive headers, applies the response-size limit, and performs the
request. The response body is passed to the isolated worker as a bounded
base64 fixture. The worker returns only a redacted source report (status,
counts, hashes, and relative links); it cannot instantiate `PaperFacts`, write
`facts.json`, read credentials, or make another network request.

## Identity, facts, and materialization

The script owns `title`, `authors`, original `abstract`, venue and publication
status, dates, DOI, official landing/PDF URLs, BibTeX, and provenance. The
canonical arXiv match rules are deliberately strict:

1. A normalized DOI must match exactly; or
2. Without DOI, normalized titles must match exactly, the first author must
   match, and the author-set similarity must be at least `0.8`.

Anything weaker is `unresolved_evidence`, not a canonical merge. After a match,
the formal DOI/proceedings/OpenReview record is canonical and the arXiv ID is
an alternate identifier. An arXiv `journal_ref` alone never changes
`publication_status`.

`materialize` re-fetches authoritative BibTeX, downloads the official body,
checks the PDF/HTML identity against the frozen title, and stores content
under the configured data directory. Only records passing `validate_materialized`
are written to `facts.json`. Renderers read this file offline; they do not
repair missing fields.

## Commands on the headless server

Use a persistent data directory and Python 3.12+:

```bash
export LLMSD_DATA_DIR=/persistent/llmsd-data
python scripts/llm_security/run_daily.py doctor
python scripts/llm_security/run_daily.py init-plan --out RUN/search-plan.json
python scripts/llm_security/run_daily.py collect \
  --plan RUN/search-plan.json --out RUN/candidates.json --data-dir "$LLMSD_DATA_DIR"
```

`doctor` probes OpenReview through the same official client factory used by
collection. It does not issue a second generic HTTP request or print either
credential. The GitHub collector workflow exposes the two variables as
optional secret-backed environment values; unset secrets do not make the job
pretend that OpenReview succeeded.

Hermes writes a selection containing only `paper_id`, `score`, `category`,
`reason`, and required `track` (`core` or `broad`), then runs:

```bash
python scripts/llm_security/run_daily.py materialize \
  --candidates RUN/candidates.json --selection RUN/selection.json \
  --facts RUN/facts.json --manifest RUN/manifest.json \
  --data-dir "$LLMSD_DATA_DIR"
python scripts/llm_security/run_daily.py outline --facts RUN/facts.json --paper-id PAPER_ID
python scripts/llm_security/run_daily.py read-section --facts RUN/facts.json \
  --paper-id PAPER_ID --section-id SECTION_ID --max-chars 12000
python scripts/llm_security/run_daily.py find --facts RUN/facts.json \
  --paper-id PAPER_ID --query "prompt injection"
python scripts/llm_security/render_and_push.py --facts RUN/facts.json \
  --manifest RUN/manifest.json --analysis RUN/analysis.json \
  --date YYYY-MM-DD --build-site
```

The GitHub Scholar workflow is optional and receives only the collection
workflow's candidate artifact. It selects at most five unresolved or
shortlisted records, uses `SERPAPI_API_KEY` from GitHub Secrets, and uploads a
separate enrichment artifact keyed by `paper_id`. Scholar failures are visible;
the artifact has `facts_written: false` and cannot alter candidate facts or
materialization. The manual workflow_dispatch path remains a one-title smoke
test.

The scheduled GitHub collection job runs on a clean headless Linux runner. It
installs the package, runs the formal adapters plus OpenReview and arXiv, and
uploads only the bounded candidate artifact. `IEEE_XPLORE_API_KEY`, when
present, enables the optional IEEE adapter; it is not required for the
no-key sources. The Scholar `workflow_run` job downloads that artifact and
can query at most five records through `SERPAPI_API_KEY`; it uploads a new
enrichment artifact and never executes an evolution candidate or writes
`facts.json`. A persistent Hermes host may consume the artifact after checking
the source reports and then run the baseline materializer locally.

### Optional headless browser evidence

Some official portals render candidate links only after JavaScript runs. On a
headless Linux host, Hermes may submit a bounded JSON request to
`scripts/llm_security/headless_discover.py` after installing Playwright and a
Chromium runtime:

```bash
python -m pip install playwright
python -m playwright install chromium
python scripts/llm_security/headless_discover.py \
  --input RUN/browser-request.json --out RUN/browser-evidence.json
```

The request contains at most ten HTTPS URLs and every host must be in the
registered official-source allowlist. Output is bounded page evidence (title,
links, and a short text excerpt) with `facts_written: false`; credentials,
arbitrary browser code, `PaperFacts`, and `facts.json` are inaccessible to the
browser layer. Treat the evidence only as a candidate URL hint and route the
paper through the normal deterministic adapter and materializer.

When direct HTTP is blocked by a registered source, the collector can opt in to
the raw-response transport with `LLMSD_HEADLESS_FALLBACK=1`. Direct HTTP remains
primary; OpenReview always remains on the official OpenReview client. The
fallback uses a fresh Playwright context with no cookies or secret headers,
allows only registered HTTPS hosts (including every redirect), and enforces a
60-second request ceiling plus the configured response-byte bound. It returns
raw HTML/JSON/PDF bytes, status, final URL, redirect chain, and SHA-256
provenance to the same deterministic adapters; it cannot construct
`PaperFacts` or write `facts.json`. To export a bounded raw artifact manually:

```bash
python scripts/llm_security/headless_discover.py --raw \
  --input RUN/browser-request.json --out RUN/browser-raw.json
```

Secret-like query parameters are rejected rather than passed to the browser.
Direct and fallback failures remain visible in the source report.

## Hermes reflection and evolution

Hermes may change query text, keyword combinations, date windows, registered
venue selection, bounded relevance filters, within-tier ranking, retry advice,
and additive reading prompt/skill fragments. A reflection must state the
observation, root cause, affected invariant, general pattern, expected metric,
counterexamples, and regression fixtures. Case-sensitive and single-paper
rules are rejected: use Unicode normalization and `casefold()`, source/schema
rules, and at least one trigger, two independent positive cases, and one
negative case.

The runtime tree is:

```text
LLMSD_DATA_DIR/evolution/
  candidates/YYYY-MM-DD/PROPOSAL_ID/{reflection.json,root-cause.md,manifest.json,overlay/,tests/}
  shadow/PROPOSAL_ID/
  active/VERSION/
  rejected/PROPOSAL_ID/
  history/
  active.json
```

The workflow is explicit and staged:

```bash
python scripts/llm_security/run_daily.py reflect --input candidate.json --data-dir "$LLMSD_DATA_DIR"
python scripts/llm_security/run_daily.py validate-evolution --version VERSION --data-dir "$LLMSD_DATA_DIR"
python scripts/llm_security/run_daily.py shadow-evolution --version VERSION --data-dir "$LLMSD_DATA_DIR"
python scripts/llm_security/run_daily.py activate-evolution --version VERSION \
  --shadow-report "$LLMSD_DATA_DIR/evolution/shadow/YYYY-MM-DD/PROPOSAL_ID/report.json" \
  --data-dir "$LLMSD_DATA_DIR"
python scripts/llm_security/run_daily.py evolution-status --data-dir "$LLMSD_DATA_DIR"
python scripts/llm_security/run_daily.py rollback-evolution --data-dir "$LLMSD_DATA_DIR"
```

Validation includes schema and protected-key scans, static hard-code checks,
candidate-owned root-cause evidence, a trigger fixture, two independent
positive fixtures and one negative fixture, baseline invariant tests,
historical and recent replay, shadow comparison, and a machine-checked
expected metric that must improve. `requires_human_change: true` is never
activatable. Source requests are limited to registered collection/list paths;
single paper IDs, DOI paths, and detail pages are rejected. Activation writes a new
immutable version and changes only the next run. Activation never overwrites a
version and never silently retries a failed candidate.

## Immutable baseline boundary

Hermes must never edit repository baseline adapters, `PaperFacts`, canonical
matching thresholds, provenance/BibTeX/PDF identity checks, HTTP broker host
and secret policy, `facts.json` materialization, fixtures, or the baseline
Hermes contract. Prompt and reading-skill overlays are additive and cannot
remove the contract that says the LLM does not own facts. A failed overlay is
disabled and recorded with an `evolution_event`; rollback isolates the
experiment and is not a fact fallback. Future self-evolution may improve
discovery and interpretation only. It must not change the baseline main logic
or any fact invariant.
