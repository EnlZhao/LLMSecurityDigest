from __future__ import annotations

import importlib.util
from pathlib import Path

from llm_security_digest.prompt import PROMPT_TEMPLATE
from llm_security_digest.papers.models import PaperFacts


def _render_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "llm_security" / "render_and_push.py"
    spec = importlib.util.spec_from_file_location("test_render_module", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _materialized_arxiv_paper() -> PaperFacts:
    return PaperFacts(
        paper_id="arxiv:2601.12345",
        source="arxiv",
        source_id="2601.12345",
        title="A Prompt Injection Defense",
        authors=["Alice Example"],
        abstract="We evaluate a prompt injection defense.",
        publication_status="preprint",
        venue=None,
        published_at="2026-01-02T00:00:00Z",
        updated_at=None,
        doi=None,
        landing_url="https://arxiv.org/abs/2601.12345",
        pdf_url="https://arxiv.org/pdf/2601.12345",
        primary_category="cs.CR",
        bibtex="@article{fixture, title={A Prompt Injection Defense}}",
        bibtex_url="https://arxiv.org/bibtex/2601.12345",
        content={"sha256": "a" * 64, "path": "content.txt"},
        collection_tier="arxiv_fallback",
        match_state="unmatched",
    )


def test_renderer_separates_research_category_from_arxiv_platform_category() -> None:
    render = _render_module()
    output = render.render_paper_md(
        _materialized_arxiv_paper(),
        {
            "category": "Prompt Injection Defense",
            "summary_zh": "研究问题、方法与证据结果。",
        },
        1,
    )

    assert "**研究类别**：Prompt Injection Defense" in output
    assert "**会议/来源**：arXiv 预印本 (2026-01-02)" in output
    assert "cs.CR" not in output
    assert output.index("**研究类别**") < output.index("**会议/来源**")


def test_analysis_prompts_require_content_based_category_and_summary() -> None:
    contract = (
        Path(__file__).resolve().parents[1] / "scripts" / "llm_security" / "hermes_prompt.md"
    ).read_text(encoding="utf-8")

    for prompt in (PROMPT_TEMPLATE, contract):
        assert "research" in prompt.lower() and "category" in prompt.lower()
        assert "arXiv" in prompt and "OpenReview" in prompt and "cs.CR" in prompt
        assert "summary_zh" in prompt
        assert "research question" in prompt.lower()
        assert "method" in prompt.lower()
        assert "evidence" in prompt.lower()
