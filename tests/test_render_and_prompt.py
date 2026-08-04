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


def _site_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_github_pages.py"
    spec = importlib.util.spec_from_file_location("test_site_module", path)
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


def test_markdown_digest_uses_manifest_buckets_for_index_and_notes() -> None:
    render = _render_module()
    first = _materialized_arxiv_paper()
    second = PaperFacts.from_dict(
        {
            **first.to_dict(),
            "paper_id": "arxiv:2601.54321",
            "source_id": "2601.54321",
            "title": "A Second Prompt Injection Defense",
            "landing_url": "https://arxiv.org/abs/2601.54321",
            "pdf_url": "https://arxiv.org/pdf/2601.54321",
        }
    )
    readme = render.render_readme(
        [first, second],
        {
            first.paper_id: {"category": "Prompt Injection Defense"},
            second.paper_id: {"category": "Other"},
        },
        "2026-08-04",
        bucket_map={first.paper_id: "main_track", second.paper_id: "others"},
        main_track_label="Static Defense against LLM Attackers",
    )

    assert "- **Static Defense against LLM Attackers**：#1" in readme
    assert "- **Others**：#2" in readme
    assert readme.count("**分组**：Static Defense against LLM Attackers") == 1
    assert readme.count("**分组**：Others") == 1
    assert render._manifest_bucket_map(
        {"selection_decisions": {"arxiv:legacy": {"track": "core"}}}
    ) == {"arxiv:legacy": "main_track"}


def test_analysis_prompts_require_content_based_category_and_summary() -> None:
    contract = (
        Path(__file__).resolve().parents[1] / "scripts" / "llm_security" / "hermes_prompt.md"
    ).read_text(encoding="utf-8")

    for prompt in (PROMPT_TEMPLATE, contract):
        normalized_prompt = " ".join(prompt.lower().split())
        assert "research" in prompt.lower() and "category" in prompt.lower()
        assert "arXiv" in prompt and "OpenReview" in prompt and "cs.CR" in prompt
        assert "summary_zh" in prompt
        assert "research question" in normalized_prompt
        assert "method" in prompt.lower()
        assert "evidence" in prompt.lower()


def test_render_and_parse_preserve_complete_multiline_analysis() -> None:
    render = _render_module()
    site = _site_module()
    paper = _materialized_arxiv_paper()
    paper = PaperFacts.from_dict(
        {
            **paper.to_dict(),
            "abstract": "First line.\n\n> A literal quote.\nFinal line.",
        }
    )
    analysis = {
        "category": "Prompt Injection Defense",
        "summary_zh": "第一段完整翻译。\n\n第二段保留换行。",
        "problem_zh": "问题第一行。\n问题第二行。",
        "contribution_zh": "贡献第一行。\n贡献第二行。",
        "method_zh": "技术第一行。\n技术第二行。",
        "result_zh": "结果第一行。\n结果第二行。",
    }

    markdown = render.render_paper_md(paper, analysis, 1)
    assert "> First line.\n>\n> > A literal quote.\n> Final line." in markdown

    parsed = site.parse_papers_from_md(markdown)
    assert len(parsed) == 1
    assert parsed[0]["abstract_en"] == paper.abstract
    assert parsed[0]["abstract_zh"] == analysis["summary_zh"]
    assert parsed[0]["problem"] == analysis["problem_zh"]
    assert parsed[0]["contribution"] == analysis["contribution_zh"]
    assert parsed[0]["method"] == analysis["method_zh"]
    assert parsed[0]["result"] == analysis["result_zh"]


def test_parser_accepts_legacy_analysis_headings_and_multiline_abstract() -> None:
    site = _site_module()
    content = """### [1]. Legacy paper

**作者**：Alice Example
**会议/来源**：arXiv 预印本 (2026-01-02)
**链接**：[论文主页](https://arxiv.org/abs/2601.12345) | [正文](https://arxiv.org/pdf/2601.12345)
**分类**：Prompt Injection Defense

**Abstract (EN — 权威来源原文)**：

> Legacy first line.
>
> Legacy second line.

**摘要 (中文，LLM 生成)**：

旧版完整中文摘要第一行。
旧版完整中文摘要第二行。

**问题（LLM 解读）**：

旧版问题第一行。
旧版问题第二行。

**方法（LLM 解读）**：

旧版方法第一行。
旧版方法第二行。

**结果（LLM 解读）**：

旧版结果第一行。
旧版结果第二行。

**贡献（LLM 解读）**：

旧版贡献第一行。
旧版贡献第二行。
"""

    parsed = site.parse_papers_from_md(content)
    assert len(parsed) == 1
    assert parsed[0]["abstract_en"] == "Legacy first line.\n\nLegacy second line."
    assert parsed[0]["abstract_zh"] == "旧版完整中文摘要第一行。\n旧版完整中文摘要第二行。"
    assert parsed[0]["problem"] == "旧版问题第一行。\n旧版问题第二行。"
    assert parsed[0]["method"] == "旧版方法第一行。\n旧版方法第二行。"
    assert parsed[0]["result"] == "旧版结果第一行。\n旧版结果第二行。"
    assert parsed[0]["contribution"] == "旧版贡献第一行。\n旧版贡献第二行。"
