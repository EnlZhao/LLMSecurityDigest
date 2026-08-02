#!/usr/bin/env python3
"""把 selected-20-clean.json 渲染成 digests/YYYY-MM-DD/{README.md, papers/*.md, bibtex.bib}，git add/commit/push。"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib import request, error

REPO = Path("/home/ubuntu/LLMSecurityDigest")
ARXIV_BIBTEX = "https://arxiv.org/bibtex/{id}"


def fetch_bibtex(arxiv_id: str) -> str:
    try:
        req = request.Request(ARXIV_BIBTEX.format(id=arxiv_id),
                              headers={"User-Agent": "Mozilla/5.0"})
        with request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8").strip()
    except Exception:
        return ""


def fallback_bibtex(p: dict) -> str:
    title = p["title"].replace("{", "").replace("}", "")
    authors = " and ".join(p.get("authors", [])[:10])
    year = (p.get("published", "") or "????")[:4]
    last = (p.get("authors", ["anon"])[0].split()[-1] if p.get("authors") else "anon")
    key = f"{last}{year}_{p['arxiv_id'].replace('/', '').replace('.', '')}"
    return f"""@misc{{{key},
  title  = {{{{{title}}}}},
  author = {{{authors}}},
  year   = {{{year}}},
  eprint = {{{p['arxiv_id']}}},
  archivePrefix = {{arXiv}},
  primaryClass  = {{{p.get('primary_category', 'cs.CR')}}},
  url    = {{https://arxiv.org/abs/{p['arxiv_id']}}}
}}"""


def render_paper_md(p: dict, idx: int) -> str:
    authors = ", ".join(p.get("authors", [])[:5])
    if len(p.get("authors", [])) > 5:
        authors += f" 等 ({len(p['authors'])} 人)"
    affils = "; ".join(sorted(set(p.get("affiliations", [])))) or "未在 arXiv 元数据中提供"
    venue = p.get("venue_or_source", f"arXiv preprint `{p.get('primary_category', 'cs.CR')}`")
    venue_date = (p.get("published", "") or "")[:10]
    classification = "顶会接收" if any(v in venue for v in ["USENIX","S&P","CCS","NDSS","NeurIPS","ICML","ICLR","AAAI","ACL","EMNLP"]) else "arXiv"

    bib = p.get("bibtex", "").strip() or fallback_bibtex(p)

    return f"""### [{idx}]. {p['title']}

**作者**：{authors}
**单位**：{affils}
**会议/来源**：{venue} ({venue_date})
**链接**：https://arxiv.org/abs/{p['arxiv_id']}
**分类**：{classification}

**Abstract (EN — 原文)**：

> {p['summary_en']}

**摘要 (中文)**：

{p.get('summary_zh', '（待补充）')}

**问题 (原文 + 中文)**：

- EN: {p.get('problem_en', '（待补充）')}
- ZH: {p.get('problem_zh', '（待补充）')}

**方法 (原文 + 中文)**：

- EN: {p.get('method_en', '（待补充）')}
- ZH: {p.get('method_zh', '（待补充）')}

**结果 (原文 + 中文)**：

- EN: {p.get('result_en', '（待补充）')}
- ZH: {p.get('result_zh', '（待补充）')}

**贡献 (原文 + 中文)**：

- EN: {p.get('contribution_en', '（待补充）')}
- ZH: {p.get('contribution_zh', '（待补充）')}

**BibTeX**：

```bibtex
{bib}
```

---
"""


def render_readme(papers: list[dict], date_str: str) -> str:
    # 分类索引
    cat_counts: dict[str, list[int]] = {}
    for i, p in enumerate(papers, 1):
        cat = p.get("category", "Other")
        cat_counts.setdefault(cat, []).append(i)

    parts = [
        f"# LLM Security Daily — {date_str}",
        "",
        f"> 20 篇高质量 LLM Security 论文 | 来源：AAAI / IEEE S&P / USENIX Security / CCS / NDSS / NeurIPS / ICML / ICLR / arXiv",
        f"> 模型：MiniMax-M3 (max reasoning) | 仓库：git@github.com:EnlZhao/LLMSecurityDigest.git",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        "",
        "## 分类索引",
        "",
    ]
    for cat, idxs in cat_counts.items():
        parts.append(f"- **{cat}**：#{', #'.join(map(str, idxs))}")
    parts.append("")
    parts.append("---")
    parts.append("")
    for i, p in enumerate(papers, 1):
        parts.append(render_paper_md(p, i))
    return "\n".join(parts).rstrip() + "\n"


def render_bibtex(papers: list[dict]) -> str:
    parts = [f"% LLM Security Daily — {time.strftime('%Y-%m-%d', time.gmtime())}", ""]
    for i, p in enumerate(papers, 1):
        bib = p.get("bibtex", "").strip() or fallback_bibtex(p)
        parts.append(f"% [{i}] {p['title']}")
        parts.append(bib)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def git_push(date_str: str) -> tuple[bool, str]:
    try:
        subprocess.run(["git", "add", f"digests/{date_str}/"], cwd=REPO, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"digest: {date_str} — LLM Security daily digest"],
            cwd=REPO, check=True, capture_output=True,
        )
        for attempt in range(1, 4):
            r = subprocess.run(["git", "push", "origin", "main"], cwd=REPO, capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
                return True, sha
            subprocess.run(["git", "pull", "--rebase"], cwd=REPO, capture_output=True)
        return False, "push failed after 3 attempts"
    except subprocess.CalledProcessError as e:
        return False, f"git error: {e.stderr.decode() if e.stderr else str(e)}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=REPO / "cache" / "selected-20-clean.json")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    args = parser.parse_args()

    if not args.input.exists():
        print(f"FATAL: input not found: {args.input}", file=sys.stderr)
        return 1

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    papers = payload.get("papers", [])
    if not papers:
        print("FATAL: no papers in input", file=sys.stderr)
        return 1

    # 拉 bibtex
    for p in papers:
        if not p.get("bibtex"):
            b = fetch_bibtex(p["arxiv_id"])
            if b:
                p["bibtex"] = b
                print(f"[bib] {p['arxiv_id']}: ok", file=sys.stderr)

    # 渲染
    out_dir = REPO / "digests" / args.date
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "papers").mkdir(exist_ok=True)
    readme = render_readme(papers, args.date)
    (out_dir / "README.md").write_text(readme, encoding="utf-8")
    bib = render_bibtex(papers)
    (out_dir / "bibtex.bib").write_text(bib, encoding="utf-8")
    for i, p in enumerate(papers, 1):
        slug = re.sub(r"[^a-z0-9]+", "-", p["title"].lower())[:40].strip("-")
        path = out_dir / "papers" / f"{i:02d}_{p['arxiv_id'].replace('/', '_')}_{slug}.md"
        path.write_text(render_paper_md(p, i), encoding="utf-8")

    print(f"[render] wrote {len(papers)} papers to {out_dir}", file=sys.stderr)

    if args.push:
        ok, info = git_push(args.date)
        print(f"[push] {'OK' if ok else 'FAIL'}: {info}", file=sys.stderr)
        if not ok:
            return 2
        # 输出 README 内容到 stdout
        sys.stdout.write(readme)
        sys.stdout.write(f"\n\n✅ 已 push 到 GitHub: `{info}` | {args.date}\n")
        sys.stdout.write("💡 回复论文编号（如 #3）开始详细阅读\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())