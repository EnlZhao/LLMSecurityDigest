#!/usr/bin/env python3
"""LLM Security Digest — GitHub Pages 静态站生成器。

输入：
- digests/YYYY-MM-DD/  → 每日双语摘要
- notes/<venue>/<...>.md → 详细笔记（含 YAML frontmatter + 配图）

输出：
- docs/index.html         主页（最新 30 天 + 标签云）
- docs/digest/YYYY-MM-DD.html
- docs/notes/<venue>/YYYY-MM-DD_<id>.html
- docs/tags/<tag>.html
- docs/rss.xml

依赖：markdown 库 + python-frontmatter（pip install markdown python-frontmatter）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

try:
    import frontmatter
except ImportError:
    print("需要安装 python-frontmatter: pip install python-frontmatter", file=sys.stderr)
    raise

try:
    import markdown
except ImportError:
    print("需要安装 markdown: pip install markdown", file=sys.stderr)
    raise

REPO = Path("/home/ubuntu/LLMSecurityDigest")
DOCS = REPO / "docs"
DIGESTS = REPO / "digests"
NOTES = REPO / "notes"
SITE_TITLE = "LLM Security Digest"
SITE_DESC = "Daily LLM Security papers — top venue & arxiv, bilingual EN/ZH summaries"
SITE_URL = "https://EnlZhao.github.io/LLMSecurityDigest"


# -----------------------------------------------------------------------------
# Markdown → HTML
# -----------------------------------------------------------------------------
def md_to_html(text: str) -> str:
    md = markdown.Markdown(extensions=[
        "fenced_code",
        "tables",
        "toc",
        "attr_list",
        "def_list",
        "footnotes",
    ])
    return md.convert(text)


# -----------------------------------------------------------------------------
# 单日摘要页
# -----------------------------------------------------------------------------
def render_digest_page(date_str: str, readme_path: Path) -> str:
    """把 digests/YYYY-MM-DD/README.md 转成 HTML 页面"""
    md = frontmatter.load(readme_path)
    content_md = md.content
    # 从 README 提取分类索引
    categories = extract_categories_from_md(content_md)

    body_html = md_to_html(content_md)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{date_str} — {SITE_TITLE}</title>
  <meta name="description" content="LLM Security daily digest for {date_str}: 20 papers with bilingual summaries">
  <link rel="stylesheet" href="{relpath(DOCS / 'digest', DOCS)}assets/style.css">
</head>
<body>
<header class="site">
  <div class="container">
    <h1><a href="{relpath(DOCS / 'digest', DOCS)}index.html">{SITE_TITLE}</a></h1>
    <nav>
      <a href="{relpath(DOCS / 'digest', DOCS)}index.html">Home</a>
      <a href="{relpath(DOCS / 'digest', DOCS)}archive.html">Archive</a>
      <a href="{relpath(DOCS / 'digest', DOCS)}rss.xml">RSS</a>
    </nav>
  </div>
</header>

<div class="container">
  <h1 style="margin-top:0">📅 {date_str}</h1>
  <p class="meta"><strong>20 篇 LLM Security 论文</strong> · 顶会 + arXiv · 双语摘要 (EN / 中文)</p>

  <h2>分类索引</h2>
  {render_category_links(categories)}

  <hr>

  {body_html}

  <p style="margin-top:2em"><a href="{relpath(DOCS / 'digest', DOCS)}index.html">← 返回首页</a></p>
</div>

<footer class="site">
  Generated automatically from <code>digests/{date_str}/README.md</code> ·
  <a href="https://github.com/EnlZhao/LLMSecurityDigest">GitHub</a> ·
  <a href="{relpath(DOCS / 'digest', DOCS)}rss.xml">RSS</a>
</footer>
</body>
</html>
"""


def extract_categories_from_md(content: str) -> dict[str, list[int]]:
    """解析 README.md 的"## 分类索引"区块，返回 {分类名: [论文编号列表]}"""
    cats = {}
    in_index = False
    for line in content.split("\n"):
        if line.startswith("## 分类索引") or line.startswith("## 分类") or "分类索引" in line:
            in_index = True
            continue
        if in_index and line.startswith("---"):
            break
        if in_index and line.strip().startswith("-"):
            m = re.match(r"-\s*\*\*(.+?)\*\*：(.+)", line.strip())
            if m:
                cat_name = m.group(1).strip()
                nums = [int(n) for n in re.findall(r"#?(\d+)", m.group(2))]
                cats[cat_name] = nums
    return cats


def render_category_links(cats: dict[str, list[int]]) -> str:
    if not cats:
        return ""
    html = "<ul>"
    for cat, nums in cats.items():
        html += f'<li><strong>{cat}</strong>：#{", #".join(map(str, nums))}</li>'
    html += "</ul>"
    return html


def relpath(from_path: Path, to_root: Path) -> str:
    """计算 from_path 相对 to_root 的相对路径（用于 HTML href/src）"""
    from_path = Path(from_path).resolve()
    to_root = Path(to_root).resolve()
    rel = Path(from_path).relative_to(to_root) if from_path.is_relative_to(to_root) else Path(".")
    return "" if str(rel) == "." else "../" * (len(rel.parts) - 1)


# -----------------------------------------------------------------------------
# 单篇笔记页
# -----------------------------------------------------------------------------
def render_note_page(note_path: Path) -> tuple[str, dict]:
    """渲染单篇笔记，返回 (html, frontmatter metadata)"""
    md = frontmatter.load(note_path)
    content_html = md_to_html(md.content)
    meta = md.metadata

    title = meta.get("title", note_path.stem)
    arxiv_id = meta.get("arxiv_id", "")
    venue = meta.get("venue", "")
    authors = meta.get("authors", [])
    date = meta.get("date", "")
    tags = meta.get("tags", [])

    front_html = ""
    if meta:
        front_html += '<div style="background:var(--code-bg);padding:1em;border-radius:6px;margin:1em 0;font-size:0.92em;">'
        for k, v in meta.items():
            if k == "tags":
                v = " ".join(f'<span class="tag">{t}</span>' for t in v)
            elif isinstance(v, list):
                v = ", ".join(str(x) for x in v)
            front_html += f'<div><strong>{k}</strong>: {v}</div>'
        front_html += "</div>"

    # 计算笔记页面的输出路径：与 md 在 NOTES 下同样的相对位置
    rel = note_path.relative_to(NOTES)
    out_path = DOCS / "notes" / rel.parent / note_path.name.replace(".md", ".html")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — {SITE_TITLE}</title>
  <meta name="description" content="{title} ({venue}, {arxiv_id})">
  <link rel="stylesheet" href="{relpath(out_path, DOCS)}assets/style.css">
</head>
<body>
<header class="site">
  <div class="container">
    <h1><a href="{relpath(out_path, DOCS)}index.html">{SITE_TITLE}</a></h1>
    <nav>
      <a href="{relpath(out_path, DOCS)}index.html">Home</a>
      <a href="{relpath(out_path, DOCS)}archive.html">Archive</a>
    </nav>
  </div>
</header>

<div class="container">
  {front_html}
  {content_html}
  <p style="margin-top:2em"><a href="{relpath(out_path, DOCS)}index.html">← 返回首页</a></p>
</div>

<footer class="site">
  Note generated by deeppapernote skill ·
  <a href="https://arxiv.org/abs/{arxiv_id}">arXiv:{arxiv_id}</a>
</footer>
</body>
</html>
"""
    return html, out_path, meta


# -----------------------------------------------------------------------------
# 首页 + Archive + RSS
# -----------------------------------------------------------------------------
def render_index() -> str:
    """主页：最新 30 天 + 标签云 + 链接到 archive"""
    # 收集所有摘要日期
    digest_dates = sorted([d.name for d in DIGESTS.iterdir() if d.is_dir()], reverse=True)[:30]
    notes = sorted(NOTES.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]

    digest_items = ""
    for date_str in digest_dates:
        n_papers = len(list((DIGESTS / date_str / "papers").glob("*.md"))) if (DIGESTS / date_str / "papers").exists() else 0
        digest_items += f'<li>📅 <a href="digest/{date_str}.html">{date_str}</a> <span class="meta">({n_papers} 篇)</span></li>'

    notes_items = ""
    for note_path in notes:
        try:
            md = frontmatter.load(note_path)
            title = md.metadata.get("title", note_path.stem)
            arxiv_id = md.metadata.get("arxiv_id", "")
            venue = md.metadata.get("venue", "")
            rel = note_path.relative_to(NOTES)
            html_path = f"notes/{rel.parent}/{note_path.stem}.html"
            notes_items += f'<li>📄 <a href="{html_path}">{title}</a> <span class="meta">{venue} · {arxiv_id}</span></li>'
        except Exception:
            continue

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{SITE_TITLE}</title>
  <meta name="description" content="{SITE_DESC}">
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header class="site">
  <div class="container">
    <h1><a href="index.html">{SITE_TITLE}</a></h1>
    <nav>
      <a href="archive.html">Archive</a>
      <a href="rss.xml">RSS</a>
      <a href="https://github.com/EnlZhao/LLMSecurityDigest">GitHub</a>
    </nav>
  </div>
</header>

<div class="container">
  <p>{SITE_DESC}。每天 06:00 自动从顶会（USENIX Security / S&P / CCS / NDSS / NeurIPS / ICML / ICLR / AAAI / ACL / EMNLP）和 arXiv 收集 20 篇高质量论文（10 篇主方向 + 10 篇大方向），双语摘要 + 用户手动触发详细精读笔记。</p>

  <h2>📅 最新摘要</h2>
  <ul class="digest-list">
    {digest_items}
  </ul>

  <h2>📄 最新详细笔记</h2>
  <ul class="digest-list">
    {notes_items}
  </ul>

  <h2>🏷️ 按方向浏览</h2>
  <div class="tag-cloud">
    <a class="tag" href="tags/llm-jailbreak.html">llm-jailbreak</a>
    <a class="tag" href="tags/llm-prompt-injection.html">llm-prompt-injection</a>
    <a class="tag" href="tags/llm-privacy.html">llm-privacy</a>
    <a class="tag" href="tags/llm-backdoor.html">llm-backdoor</a>
    <a class="tag" href="tags/llm-alignment.html">llm-alignment</a>
    <a class="tag" href="tags/llm-agent-security.html">llm-agent-security</a>
  </div>

  <h2>🔗 关于</h2>
  <p>本站由 Hermes Agent 自动构建。数据源：arxiv <code>co:</code> 顶会标注 + 关键词检索 + Semantic Scholar 机构补全。摘要由 LLM 推理筛选与生成。详细笔记由 <code>deeppapernote</code> skill 生成。</p>
  <p>主方向当前配置：<code>LLM as attacker 的静态防御</code>。修改主方向：编辑 <code>~/.hermes/profile_user.json</code>。</p>
</div>

<footer class="site">
  自动构建于 {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} ·
  <a href="https://github.com/EnlZhao/LLMSecurityDigest">Source on GitHub</a>
</footer>
</body>
</html>
"""


def render_rss() -> str:
    digest_dates = sorted([d.name for d in DIGESTS.iterdir() if d.is_dir()], reverse=True)[:20]
    items = ""
    for date_str in digest_dates:
        readme = DIGESTS / date_str / "README.md"
        if not readme.exists():
            continue
        # 取首段作为 description
        content = readme.read_text(encoding="utf-8")
        first_para = ""
        for line in content.split("\n"):
            if line.strip() and not line.startswith("#") and not line.startswith(">"):
                first_para = line.strip()
                break
        items += f"""    <item>
      <title>{date_str} LLM Security Digest</title>
      <link>{SITE_URL}/digest/{date_str}.html</link>
      <description>{first_para[:300]}</description>
      <pubDate>{date_str}T06:00:00Z</pubDate>
      <guid>{SITE_URL}/digest/{date_str}.html</guid>
    </item>
"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{SITE_TITLE}</title>
    <link>{SITE_URL}</link>
    <description>{SITE_DESC}</description>
    <language>zh-CN</language>
    <lastBuildDate>{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}</lastBuildDate>
{items}
  </channel>
</rss>
"""


def render_archive() -> str:
    digest_dates = sorted([d.name for d in DIGESTS.iterdir() if d.is_dir()], reverse=True)
    items = ""
    for date_str in digest_dates:
        items += f'<li>📅 <a href="digest/{date_str}.html">{date_str}</a></li>'

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>Archive — {SITE_TITLE}</title>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header class="site">
  <div class="container">
    <h1><a href="index.html">{SITE_TITLE}</a></h1>
  </div>
</header>

<div class="container">
  <h1>Archive</h1>
  <ul class="digest-list">
    {items}
  </ul>
  <p><a href="index.html">← 返回首页</a></p>
</div>
</body>
</html>
"""


# -----------------------------------------------------------------------------
# 主入口
# -----------------------------------------------------------------------------
def build() -> int:
    DOCS.mkdir(exist_ok=True)
    (DOCS / "digest").mkdir(exist_ok=True)
    (DOCS / "notes").mkdir(exist_ok=True)
    (DOCS / "tags").mkdir(exist_ok=True)
    (DOCS / "assets").mkdir(exist_ok=True)

    print("[build] rendering digest pages...")
    n_digests = 0
    for date_dir in sorted(DIGESTS.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        date_str = date_dir.name
        readme = date_dir / "README.md"
        if not readme.exists():
            continue
        html = render_digest_page(date_str, readme)
        (DOCS / "digest" / f"{date_str}.html").write_text(html, encoding="utf-8")
        n_digests += 1
    print(f"[build] {n_digests} digest pages")

    print("[build] rendering note pages...")
    n_notes = 0
    for note_path in NOTES.rglob("*.md"):
        try:
            html, out_path, meta = render_note_page(note_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(html, encoding="utf-8")
            # 同时把 figures/ 子目录复制到 docs/notes/.../ 下，修正图片相对路径
            src_figs = note_path.parent / "figures"
            if src_figs.exists():
                import shutil
                dst_figs = out_path.parent / "figures"
                if dst_figs.exists():
                    shutil.rmtree(dst_figs)
                shutil.copytree(src_figs, dst_figs)
            n_notes += 1
        except Exception as e:
            print(f"  [warn] {note_path}: {e}", file=sys.stderr)
    print(f"[build] {n_notes} note pages")

    print("[build] rendering index/archive/rss...")
    (DOCS / "index.html").write_text(render_index(), encoding="utf-8")
    (DOCS / "archive.html").write_text(render_archive(), encoding="utf-8")
    (DOCS / "rss.xml").write_text(render_rss(), encoding="utf-8")

    print(f"[build] done → {DOCS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())