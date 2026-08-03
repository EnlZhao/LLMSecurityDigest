#!/usr/bin/env python3
"""Render a verified, frozen facts snapshot. This command performs no network I/O."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_security_digest.papers.models import PaperFacts
from llm_security_digest.papers.pipeline import load_analysis, write_json


def _analysis_value(analysis: dict, key: str, default: str = "（待解读）") -> str:
    value = analysis.get(key)
    return str(value).strip() if value is not None and str(value).strip() else default


def render_paper_md(paper: PaperFacts, analysis: dict, idx: int) -> str:
    paper.validate_materialized()
    authors = ", ".join(paper.authors)
    source = paper.venue or f"arXiv preprint `{paper.primary_category or 'unknown'}`"
    classification = {"accepted": "会议接收", "published": "正式发表", "preprint": "arXiv 预印本"}[paper.publication_status]
    published_date = (paper.published_at or "")[:10]
    category = _analysis_value(analysis, "category", "Other")
    scholar_link = paper.platform_links.get("google_scholar", "")
    scholar_line = f" | [Google Scholar]({scholar_link})" if scholar_link else ""
    source_comment = (
        f"**来源备注（权威来源原文）**：{paper.source_comment}\n"
        if paper.source_comment
        else ""
    )
    return f"""### [{idx}]. {paper.title}

**作者**：{authors}
**会议/来源**：{source} ({published_date})
{source_comment}**链接**：[论文主页]({paper.landing_url}) | [正文]({paper.pdf_url}){scholar_line}
**分类**：{classification}
**研究类别**：{category}

**Abstract (EN — 权威来源原文)**：

> {paper.abstract}

**摘要 (中文，LLM 生成)**：

{_analysis_value(analysis, 'summary_zh')}

**问题（LLM 解读）**：

{_analysis_value(analysis, 'problem_zh')}

**方法（LLM 解读）**：

{_analysis_value(analysis, 'method_zh')}

**结果（LLM 解读）**：

{_analysis_value(analysis, 'result_zh')}

**贡献（LLM 解读）**：

{_analysis_value(analysis, 'contribution_zh')}

**BibTeX（权威端点原文）**：

```bibtex
{paper.bibtex}
```

---
"""


def render_readme(papers: list[PaperFacts], analyses: dict[str, dict], date_str: str) -> str:
    categories: dict[str, list[int]] = {}
    for index, paper in enumerate(papers, 1):
        category = _analysis_value(analyses.get(paper.paper_id, {}), "category", "Other")
        categories.setdefault(category, []).append(index)
    parts = [
        f"# LLM Security Daily — {date_str}",
        "",
        f"> {len(papers)} 篇通过元数据、BibTeX 与正文身份校验的论文",
        "> 事实字段由确定性脚本生成；翻译与解读由 LLM 生成并明确标注",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        "## 分类索引",
        "",
    ]
    for category, indexes in categories.items():
        parts.append(f"- **{category}**：#{', #'.join(map(str, indexes))}")
    parts.extend(["", "---", ""])
    for index, paper in enumerate(papers, 1):
        parts.append(render_paper_md(paper, analyses.get(paper.paper_id, {}), index))
    return "\n".join(parts).rstrip() + "\n"


def git_push(repo: Path, date_str: str) -> tuple[bool, str]:
    subprocess.run(["git", "add", f"digests/{date_str}/", "docs/"], cwd=repo, check=True)
    commit = subprocess.run(
        ["git", "commit", "-m", f"digest: {date_str} LLM Security daily digest"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if commit.returncode != 0:
        return False, commit.stderr.strip() or commit.stdout.strip()
    push = subprocess.run(["git", "push", "origin", "main"], cwd=repo, capture_output=True, text=True, timeout=60)
    if push.returncode != 0:
        return False, push.stderr.strip()
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    return True, sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--build-site", action="store_true")
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()

    facts_payload = json.loads(args.facts.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    raw_papers = facts_payload.get("papers") if isinstance(facts_payload, dict) else None
    if not isinstance(raw_papers, list) or not isinstance(manifest, dict):
        print("FATAL: invalid facts or manifest snapshot", file=sys.stderr)
        return 1
    papers = [PaperFacts.from_dict(value) for value in raw_papers]
    if not papers:
        print("FATAL: no verified papers", file=sys.stderr)
        return 1
    decisions = manifest.get("selection_decisions")
    if not isinstance(decisions, dict):
        print("FATAL: invalid materialize decisions", file=sys.stderr)
        return 1
    decision_ids = set(decisions)
    paper_ids = {paper.paper_id for paper in papers}
    if (
        len(paper_ids) != len(papers)
        or manifest.get("status") != "ok"
        or manifest.get("published") != len(papers)
        or decision_ids != paper_ids
    ):
        print("FATAL: facts and materialize manifest do not agree", file=sys.stderr)
        return 1
    analyses = load_analysis(args.analysis, {paper.paper_id for paper in papers})

    output_dir = args.repo.resolve() / "digests" / args.date
    papers_dir = output_dir / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    readme = render_readme(papers, analyses, args.date)
    (output_dir / "README.md").write_text(readme, encoding="utf-8")
    bibtex_parts = [f"% LLM Security Daily — {args.date}", ""]
    for index, paper in enumerate(papers, 1):
        bibtex_parts.extend([f"% [{index}] {paper.title}", paper.bibtex or "", ""])
        slug = re.sub(r"[^a-z0-9]+", "-", paper.title.casefold())[:48].strip("-")
        filename = f"{index:02d}_{re.sub(r'[^a-zA-Z0-9_.-]+', '_', paper.source_id)}_{slug}.md"
        (papers_dir / filename).write_text(render_paper_md(paper, analyses.get(paper.paper_id, {}), index), encoding="utf-8")
    (output_dir / "bibtex.bib").write_text("\n".join(bibtex_parts).rstrip() + "\n", encoding="utf-8")
    write_json(output_dir / "facts.json", facts_payload)
    write_json(output_dir / "analysis.json", {"papers": list(analyses.values())})
    write_json(output_dir / "manifest.json", manifest)
    print(f"[render] wrote {len(papers)} verified papers to {output_dir}", file=sys.stderr)

    if args.build_site:
        subprocess.run([sys.executable, str(args.repo / "scripts" / "build_github_pages.py")], cwd=args.repo, check=True)
    if args.push:
        ok, info = git_push(args.repo, args.date)
        print(f"[push] {'OK' if ok else 'FAIL'}: {info}", file=sys.stderr)
        return 0 if ok else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
