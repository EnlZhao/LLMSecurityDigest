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
search plan, then run the script's collect command. arXiv and OpenReview are the
primary no-key sources. Rank more than {n} candidate paper_ids. The materialize
command downloads and identity-validates full text and obtains authoritative
BibTeX. It invokes SerpAPI only for that ranked shortlist, never for discovery.

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
