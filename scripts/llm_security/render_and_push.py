#!/usr/bin/env python3
"""Render a verified, frozen facts snapshot. This command performs no network I/O."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_security_digest.papers.models import PaperFacts, facts_sha256
from llm_security_digest.papers.pipeline import load_analysis, write_json

BUCKETS = ("main_track", "others")


def _analysis_value(analysis: dict, key: str, default: str = "（待解读）") -> str:
    value = analysis.get(key)
    if value is None:
        return default
    text = str(value)
    return text if text.strip() else default


def _markdown_blockquote(text: str) -> str:
    """Keep every source line inside the Markdown blockquote."""
    return "\n".join(
        f"> {line}" if line else ">"
        for line in str(text).split("\n")
    )


def _manifest_bucket_map(manifest: dict) -> dict[str, str]:
    decisions = manifest.get("selection_decisions")
    if not isinstance(decisions, dict):
        return {}
    assignments: dict[str, str] = {}
    for decision_key, decision in decisions.items():
        if not isinstance(decision, dict):
            continue
        bucket = str(decision.get("bucket", "")).strip().lower().replace("-", "_")
        if not bucket:
            track = str(decision.get("track", "")).strip().lower().replace("-", "_")
            bucket = {"core": "main_track", "broad": "others"}.get(track, "others")
        normalized = "main_track" if bucket == "main_track" else "others"
        for paper_id in (decision_key, decision.get("paper_id")):
            if paper_id:
                assignments[str(paper_id).strip()] = normalized
    return assignments


def _bucket_label(bucket: str, main_track_label: str) -> str:
    if bucket == "main_track":
        return main_track_label or "Main Track"
    return "Others"


def render_paper_md(
    paper: PaperFacts,
    analysis: dict,
    idx: int,
    *,
    bucket: str = "others",
    main_track_label: str = "",
) -> str:
    # Older materialized snapshots used a text sentinel for an absent DOI.
    # Normalize it only on the in-memory model; the frozen facts snapshot is
    # written back verbatim and remains the source of record.
    if paper.doi == "not_provided":
        paper.doi = None
    paper.validate_materialized()
    authors = ", ".join(paper.authors)
    source = f"正式 venue：{paper.venue}" if paper.venue else "arXiv 预印本"
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
**研究类别**：{category}
**分组**：{_bucket_label(bucket, main_track_label)}
**会议/来源**：{source} ({published_date})
{source_comment}**链接**：[论文主页]({paper.landing_url}) | [正文]({paper.pdf_url}){scholar_line}
**分类**：{classification}

**Abstract (EN — 权威来源原文)**：

{_markdown_blockquote(paper.abstract)}

**Abstract (ZH — 学术中文翻译，LLM 生成)**：

{_analysis_value(analysis, 'summary_zh')}

**问题 / Problem（LLM 解读）**：

{_analysis_value(analysis, 'problem_zh')}

**创新与贡献 / Innovation / Contribution（LLM 解读）**：

{_analysis_value(analysis, 'contribution_zh')}

**技术细节 / Technical details（LLM 解读）**：

{_analysis_value(analysis, 'method_zh')}

**实验结果 / Experiment results（LLM 解读）**：

{_analysis_value(analysis, 'result_zh')}

**BibTeX（权威端点原文）**：

```bibtex
{paper.bibtex}
```

---
"""


def render_readme(
    papers: list[PaperFacts],
    analyses: dict[str, dict],
    date_str: str,
    *,
    bucket_map: dict[str, str] | None = None,
    main_track_label: str = "",
) -> str:
    grouped: dict[str, list[int]] = {bucket: [] for bucket in BUCKETS}
    for index, paper in enumerate(papers, 1):
        bucket = (bucket_map or {}).get(paper.paper_id, "others")
        grouped["main_track" if bucket == "main_track" else "others"].append(index)
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
    for bucket in BUCKETS:
        label = _bucket_label(bucket, main_track_label)
        parts.append(f"- **{label}**：#{', #'.join(map(str, grouped[bucket]))}")
    parts.extend(["", "---", ""])
    for index, paper in enumerate(papers, 1):
        parts.append(
            render_paper_md(
                paper,
                analyses.get(paper.paper_id, {}),
                index,
                bucket=(bucket_map or {}).get(paper.paper_id, "others"),
                main_track_label=main_track_label,
            )
        )
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


def _validate_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value, flags=re.ASCII):
        raise ValueError("date must be an ASCII YYYY-MM-DD value")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date must be a real calendar date") from exc
    return value


def _validated_repo(value: Path) -> Path:
    try:
        repo = Path(value).expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid repository path: {value}") from exc
    if not repo.is_dir():
        raise ValueError(f"repository path is not an existing directory: {value}")
    return repo


def _validated_output_dir(repo: Path, date_str: str) -> Path:
    digests = repo / "digests"
    if digests.is_symlink():
        raise ValueError("repository digests directory must not be a symlink")
    if digests.exists() and not digests.is_dir():
        raise ValueError("repository digests path is not a directory")
    try:
        output_dir = (digests / date_str).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("invalid digest output path") from exc
    try:
        output_dir.relative_to(repo)
    except ValueError as exc:
        raise ValueError("digest output path escapes the repository") from exc
    return output_dir


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

    try:
        date_str = _validate_date(args.date)
        repo = _validated_repo(args.repo)
        output_dir = _validated_output_dir(repo, date_str)
    except ValueError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1

    facts_payload = json.loads(args.facts.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    raw_papers = facts_payload.get("papers") if isinstance(facts_payload, dict) else None
    if not isinstance(raw_papers, list) or not isinstance(manifest, dict):
        print("FATAL: invalid facts or manifest snapshot", file=sys.stderr)
        return 1
    expected_facts_hash = manifest.get("facts_sha256")
    try:
        actual_facts_hash = facts_sha256(facts_payload)
    except (TypeError, ValueError):
        print("FATAL: invalid facts snapshot", file=sys.stderr)
        return 1
    if not isinstance(expected_facts_hash, str) or expected_facts_hash != actual_facts_hash:
        print("FATAL: facts hash is missing or does not match the manifest", file=sys.stderr)
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
    bucket_map = _manifest_bucket_map(manifest)
    main_track_label = str(manifest.get("main_track", "")).strip()

    papers_dir = output_dir / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    readme = render_readme(
        papers,
        analyses,
        date_str,
        bucket_map=bucket_map,
        main_track_label=main_track_label,
    )
    (output_dir / "README.md").write_text(readme, encoding="utf-8")
    bibtex_parts = [f"% LLM Security Daily — {date_str}", ""]
    for index, paper in enumerate(papers, 1):
        bibtex_parts.extend([f"% [{index}] {paper.title}", paper.bibtex or "", ""])
        slug = re.sub(r"[^a-z0-9]+", "-", paper.title.casefold())[:48].strip("-")
        filename = f"{index:02d}_{re.sub(r'[^a-zA-Z0-9_.-]+', '_', paper.source_id)}_{slug}.md"
        (papers_dir / filename).write_text(
            render_paper_md(
                paper,
                analyses.get(paper.paper_id, {}),
                index,
                bucket=bucket_map.get(paper.paper_id, "others"),
                main_track_label=main_track_label,
            ),
            encoding="utf-8",
        )
    (output_dir / "bibtex.bib").write_text("\n".join(bibtex_parts).rstrip() + "\n", encoding="utf-8")
    write_json(output_dir / "facts.json", facts_payload)
    write_json(output_dir / "analysis.json", {"papers": list(analyses.values())})
    write_json(output_dir / "manifest.json", manifest)
    print(f"[render] wrote {len(papers)} verified papers to {output_dir}", file=sys.stderr)

    if args.build_site:
        subprocess.run([sys.executable, str(repo / "scripts" / "build_github_pages.py")], cwd=repo, check=True)
    if args.push:
        ok, info = git_push(repo, date_str)
        print(f"[push] {'OK' if ok else 'FAIL'}: {info}", file=sys.stderr)
        return 0 if ok else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
