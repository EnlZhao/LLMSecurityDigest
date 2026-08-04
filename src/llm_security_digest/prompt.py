from __future__ import annotations

from datetime import date
from pathlib import Path

from . import config


PROMPT_TEMPLATE = """You orchestrate the LLM Security Daily run for {today}.

Repository: {repo}
Run directory: {run_dir}
Target: up to {n} verified papers; fewer is correct when verification fails.

The repository script owns all facts. You may choose queries, filter keywords,
rank candidates, categorize papers, and write Chinese interpretations.
Never create or modify title, authors, abstract, venue, publication status, DOI, URL, or BibTeX.
Never use curl or browser output to fill those fields.
Search themes include jailbreak, prompt injection, backdoor, privacy, and agent security.

Read and follow this contract before doing anything:
{contract}

Use absolute paths and keep intermediate JSON in {run_dir}. Start by creating a
search plan, then run the script's collect command. Official formal venues are
queried first; arXiv is a preprint/fallback source and journal_ref is only
unverified evidence. Rank more than {n} candidate paper_ids. The materialize
command downloads and identity-validates full text and obtains authoritative
BibTeX. It invokes SerpAPI only for that ranked shortlist, never for discovery.

Within each permitted tier, rank by topical relevance, technical novelty,
methodological clarity, evidence for the claimed result, and reader value. A
venue/status label is script-owned evidence, not a substitute for reading the
verified abstract and bounded full text. Translate the complete authoritative
abstract into academic Chinese in `summary_zh`; do not summarize it in English,
truncate it, or insert an ellipsis. In Chinese analysis, distinguish what the
frozen paper directly supports from your interpretation and state uncertainty
when the retrieved sections do not support a conclusion.

Derive `category` as a research-direction label from the paper's title,
authoritative abstract, and the bounded full-text sections you read. Do not use
platform or source labels such as `arXiv`, `OpenReview`, a venue name, or a
subject code such as `cs.CR` as a category. Derive `problem_zh` (the research
question, research problem, and gap), `contribution_zh`, `method_zh`, and
`result_zh` from the complete Chinese
academic synthesis and the sections you read, not by copying fragments of the
English abstract. Write each field completely; there is no character budget
and no display-layer truncation. The rendered output groups papers by research
direction first, then marks each paper's formal venue or arXiv preprint and
publication status.

Hermes may propose strategy-only evolution overlays with `reflect`; it may not
write facts, HTTP endpoints, credentials, or single-paper title/DOI/date values.
Run `validate-evolution` and `shadow-evolution` before activation. Activation is
atomic and takes effect on the next run; rollback is an explicit history event.

For each verified paper, inspect its outline and read bounded sections. Do not
put an entire paper into one model request. Write analysis.json containing only
paper_id, category, summary_zh, problem_zh, method_zh, result_zh, and
contribution_zh. Then invoke the offline renderer. A rejected paper remains
rejected, and no missing slot may be filled with invented or unverified data.
"""


def build_prompt(run_dir: Path, today: date) -> str:
    repo = config.PROJECT_ROOT.resolve()
    return PROMPT_TEMPLATE.format(
        today=today.isoformat(),
        run_dir=run_dir.resolve(),
        repo=repo,
        contract=repo / "scripts" / "llm_security" / "hermes_prompt.md",
        n=config.PAPERS_PER_DAY,
    )
