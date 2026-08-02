#!/usr/bin/env python3
"""LLM Security Digest — GitHub Pages 静态站生成器（两栏布局版）。

布局参考 Notion / mkdocs-material：
- 左侧 sticky 侧边栏（TOC + 论文跳转）
- 右侧主内容区（论文卡片）
- header 极简 + 字体清晰
- KaTeX 渲染 LaTeX
- 响应式（移动端折叠侧边栏）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

try:
    import frontmatter
except ImportError:
    raise ImportError("pip install python-frontmatter")

try:
    import markdown
    from markdown.extensions.toc import TocExtension
except ImportError:
    raise ImportError("pip install markdown")

REPO = Path("/home/ubuntu/LLMSecurityDigest")
DOCS = REPO / "docs"
DIGESTS = REPO / "digests"
NOTES = REPO / "notes"

SITE_TITLE = "LLM Security Digest"
SITE_DESC = "Daily LLM Security papers — top venue & arxiv, bilingual EN/ZH summaries"
SITE_URL = "https://EnlZhao.github.io/LLMSecurityDigest"

KATEX_HEAD = """
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body, {delimiters: [{left: '$$', right: '$$', display: true}, {left: '$', right: '$', display: false}], throwOnError: false});"></script>
"""


def md_to_html(text: str) -> str:
    md = markdown.Markdown(extensions=[
        "fenced_code", "tables", "sane_lists", "nl2br",
        "attr_list", TocExtension(toc_depth="2-3", permalink=False),
    ])
    return md.convert(text)


def extract_toc(html: str) -> str:
    """Extract TOC items from markdown's generated HTML."""
    # Re-run markdown to get TOC only
    md = markdown.Markdown(extensions=[
        "fenced_code", "tables", "sane_lists", "nl2br",
        "attr_list", TocExtension(toc_depth="2-3", permalink=False),
    ])
    md.convert(html if False else "")  # reset
    # Easier: use the toc extension directly
    import io
    md2 = markdown.Markdown(extensions=[TocExtension(toc_depth="2-3", permalink=False)])
    md2.convert(text_for_toc)
    return md2.toc_tokens if hasattr(md2, 'toc_tokens') else ""


def html_page(title: str, body: str, description: str = SITE_DESC, body_class: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — {SITE_TITLE}</title>
  <meta name="description" content="{description}">
  <link rel="stylesheet" href="assets/style.css">
  {KATEX_HEAD}
</head>
<body class="{body_class}">
{body}
</body>
</html>
"""


def site_header(active: str = "home") -> str:
    return f"""
<header class="site-header">
  <div class="header-inner">
    <a href="index.html" class="brand">
      <span class="brand-mark">LS</span>
      <span class="brand-text">{SITE_TITLE}</span>
    </a>
    <nav class="primary-nav">
      <a href="index.html" class="{'active' if active == 'home' else ''}">Home</a>
      <a href="archive.html" class="{'active' if active == 'archive' else ''}">Archive</a>
      <a href="rss.xml">RSS</a>
      <a href="https://github.com/EnlZhao/LLMSecurityDigest">GitHub</a>
    </nav>
  </div>
</header>
"""


def site_footer() -> str:
    return """
<footer class="site-footer">
  <div class="footer-inner">
    <div class="footer-col">
      <strong>LLM Security Digest</strong>
      <p class="muted">Daily LLM Security papers · 双语摘要 · Top venue + arXiv</p>
    </div>
    <div class="footer-col">
      <strong>导航</strong>
      <p><a href="index.html">Home</a> · <a href="archive.html">Archive</a> · <a href="rss.xml">RSS</a> · <a href="https://github.com/EnlZhao/LLMSecurityDigest">GitHub</a></p>
    </div>
    <div class="footer-col">
      <strong>配置</strong>
      <p class="muted">主方向：<code>LLM as attacker 的静态防御</code></p>
      <p class="muted">修改：编辑 <code>~/.hermes/profile_user.json</code></p>
    </div>
  </div>
</footer>
"""


# -----------------------------------------------------------------------------
# 摘要页（每日 20 篇）— 两栏：左侧 sidebar（TOC + 分类跳转），右侧论文卡片
# -----------------------------------------------------------------------------
def render_digest_page(date_str: str, readme_path: Path) -> str:
    md = frontmatter.load(readme_path)
    body_html = md_to_html(md.content)
    papers = parse_papers_from_md(md.content)

    # Sidebar: TOC + category jump + paper jump
    sidebar_html = render_digest_sidebar(papers, date_str)

    # Hero + paper cards
    main_html = render_digest_main(papers, date_str)

    body = f"""
<div class="two-col">
  <aside class="sidebar">
    {sidebar_html}
  </aside>
  <main class="main">
    {main_html}
  </main>
</div>
"""
    body_wrapped = site_header() + body + site_footer()
    return html_page(date_str, body_wrapped, description=f"LLM Security daily digest for {date_str}")


def parse_papers_from_md(content: str) -> list[dict]:
    """Parse each paper section: number, title, category, abstract_zh, abstract_en."""
    papers = []
    # Each paper starts with ### [N].
    pattern = re.compile(
        r"### \[(\d+)\]\.\s*(.+?)\n(.*?)(?=\n### \[|\n## |$)", re.DOTALL
    )
    for m in pattern.finditer(content):
        num = int(m.group(1))
        title = m.group(2).strip()
        body = m.group(3)

        paper = {"num": num, "title": title, "category": "", "abstract_en": "", "abstract_zh": "", "summary": ""}

        # Category from "**会议/来源**" or "**分类**：..."
        m_cat = re.search(r"\*\*分类\*\*[：:]\s*(.+)", body)
        if m_cat:
            paper["category"] = m_cat.group(1).strip()

        # Find Abstract block
        m_abs = re.search(r"\*\*Abstract \(EN[^\)]*\)\*\*[：:]\s*(.+?)(?=\n\*\*摘要 \(中文\)|\n\*\*问题|\Z)", body, re.DOTALL)
        if m_abs:
            paper["abstract_en"] = m_abs.group(1).strip().strip(">").strip()
        m_abs_zh = re.search(r"\*\*摘要 \(中文\)\*\*[：:]\s*(.+?)(?=\n\*\*问题|\Z)", body, re.DOTALL)
        if m_abs_zh:
            paper["abstract_zh"] = m_abs_zh.group(1).strip()

        papers.append(paper)
    return papers


def render_digest_sidebar(papers: list[dict], date_str: str) -> str:
    # Group by category
    by_cat = {}
    for p in papers:
        cat = p["category"] or "Other"
        by_cat.setdefault(cat, []).append(p)

    cat_html = '<div class="sidebar-section"><div class="sidebar-title">Categories</div><ul class="sidebar-cat-list">'
    for cat, ps in by_cat.items():
        anchor = slugify(cat)
        tag_cls = category_tag_class(cat)
        cat_html += f'<li><a class="cat-pill tag-{tag_cls}" href="#cat-{anchor}">{cat}</a> <span class="muted">({len(ps)})</span></li>'
    cat_html += "</ul></div>"

    # Paper list
    paper_html = '<div class="sidebar-section"><div class="sidebar-title">20 Papers</div><ul class="sidebar-paper-list">'
    for p in papers:
        title_short = p["title"][:50] + ("…" if len(p["title"]) > 50 else "")
        paper_html += f'<li><a href="#paper-{p["num"]}"><span class="num">#{p["num"]}</span> {escape_html(title_short)}</a></li>'
    paper_html += "</ul></div>"

    return f"""
<nav class="sidebar-toc">
  <div class="sidebar-section">
    <div class="sidebar-title">📅 {date_str}</div>
    <p class="sidebar-meta muted">20 papers · 顶会 + arXiv</p>
  </div>
  {cat_html}
  {paper_html}
</nav>
"""


def render_digest_main(papers: list[dict], date_str: str) -> str:
    by_cat = {}
    for p in papers:
        cat = p["category"] or "Other"
        by_cat.setdefault(cat, []).append(p)

    html = f"""
<header class="content-header">
  <div class="date-badge">{date_str}</div>
  <h1 class="page-title">LLM Security Daily</h1>
  <p class="lead">{len(papers)} 篇论文 · 顶会接收 + arXiv · 双语摘要（EN / 中文）</p>
  <div class="quick-stats">
    <span class="stat"><strong>{len(papers)}</strong> papers</span>
    <span class="stat"><strong>{len(by_cat)}</strong> categories</span>
    <span class="stat"><strong>EN+ZH</strong> bilingual</span>
  </div>
</header>
"""

    for cat, ps in by_cat.items():
        anchor = slugify(cat)
        tag_cls = category_tag_class(cat)
        html += f'<section id="cat-{anchor}" class="cat-section"><h2 class="cat-heading"><span class="cat-pill tag-{tag_cls}">{cat}</span><span class="muted">· {len(ps)} 篇</span></h2>'
        for p in ps:
            html += render_paper_card(p)
        html += "</section>"

    return html


def render_paper_card(p: dict) -> str:
    n = p["num"]
    title = escape_html(p["title"])
    cat = escape_html(p["category"])
    abs_en = p.get("abstract_en", "").strip()
    abs_zh = p.get("abstract_zh", "").strip()

    en_html = ""
    if abs_en:
        # Strip blockquote markers
        en_text = abs_en.replace("> ", "").strip()
        en_text = re.sub(r"\n\s*\n", "\n\n", en_text)
        en_html = f'<details class="abs-en"><summary>Abstract (EN) · 原文</summary><blockquote>{escape_html(en_text[:1200])}</blockquote></details>'

    zh_html = ""
    if abs_zh:
        zh_html = f'<div class="abs-zh">{escape_html(abs_zh[:600])}{("…" if len(abs_zh) > 600 else "")}</div>'
    elif abs_en:
        zh_html = f'<div class="abs-zh muted">（中文摘要未生成）</div>'

    return f"""
<article id="paper-{n}" class="paper-card">
  <div class="card-head">
    <span class="paper-num">#{n}</span>
    <h3 class="paper-title">{title}</h3>
    <a class="anchor-mark" href="#paper-{n}" title="Permalink">§</a>
  </div>
  <div class="card-meta muted">{cat}</div>
  <div class="card-body">
    {zh_html}
    {en_html}
  </div>
</article>
"""


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def escape_html(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


def category_tag_class(cat: str) -> str:
    c = cat.lower()
    if "jailbreak" in c or "prompt injection" in c:
        return "jailbreak"
    if "privacy" in c or "inference" in c:
        return "privacy"
    if "backdoor" in c or "adversarial" in c:
        return "backdoor"
    if "alignment" in c or "safety" in c:
        return "alignment"
    if "agent" in c:
        return "agent"
    return "llm-sec"


# -----------------------------------------------------------------------------
# 笔记页 — 两栏：左侧 TOC（按 paper section 自动生成），右侧正文
# -----------------------------------------------------------------------------
def render_note_page(note_path: Path):
    md = frontmatter.load(note_path)
    content_html = md_to_html(md.content)
    meta = md.metadata

    title = meta.get("title") or note_path.stem
    arxiv_id = meta.get("arxiv_id", "")
    venue = meta.get("venue", "")
    authors = meta.get("authors", [])
    tags = meta.get("tags", [])

    # Extract H2 sections for TOC
    toc_items = []
    for m in re.finditer(r"<h2[^>]*>(.+?)</h2>", content_html):
        text = m.group(1)
        anchor = slugify(strip_tags(text))
        toc_items.append((text, anchor))

    toc_html = '<nav class="sidebar-toc"><div class="sidebar-section"><div class="sidebar-title">📑 Sections</div><ul class="sidebar-toc-list">'
    for text, anchor in toc_items:
        toc_html += f'<li><a href="#{anchor}">{escape_html(text)}</a></li>'
    toc_html += "</ul>"

    # Tags in sidebar
    if tags:
        toc_html += '<div class="sidebar-section"><div class="sidebar-title">🏷️ Tags</div><div class="tag-cloud">'
        for t in tags:
            if isinstance(t, str):
                toc_html += f'<span class="tag">{escape_html(t)}</span>'
        toc_html += "</div></div>"

    # External links
    toc_html += '<div class="sidebar-section"><div class="sidebar-title">🔗 Links</div>'
    if arxiv_id:
        toc_html += f'<p><a href="https://arxiv.org/abs/{arxiv_id}" class="link-ext">arXiv:{arxiv_id}</a></p>'
    toc_html += '<p><a href="../index.html" class="link-ext">← Back to Home</a></p>'
    toc_html += "</div></nav>"

    # Add anchor IDs to H2 sections in content
    content_with_anchors = re.sub(
        r"<h2([^>]*)>(.+?)</h2>",
        lambda m: f'<h2{m.group(1)} id="{slugify(strip_tags(m.group(2)))}">{m.group(2)}</h2>',
        content_html,
    )

    # Meta bar at top of main content
    meta_bar = '<header class="content-header">'
    meta_bar += f'<div class="venue-badge">{escape_html(venue)}</div>' if venue else ""
    meta_bar += f'<h1 class="paper-title-h1">{escape_html(title)}</h1>'
    if authors:
        meta_bar += f'<p class="paper-authors">{", ".join(str(a) for a in authors)}</p>'
    if arxiv_id:
        meta_bar += f'<p class="paper-arxiv"><a href="https://arxiv.org/abs/{arxiv_id}">arXiv:{arxiv_id}</a></p>'
    meta_bar += "</header>"

    body = f"""
<div class="two-col">
  <aside class="sidebar">
    {toc_html}
  </aside>
  <main class="main">
    {meta_bar}
    <article class="note-content">
      {content_with_anchors}
    </article>
  </main>
</div>
"""
    rel = note_path.relative_to(NOTES)
    out_path = DOCS / "notes" / rel.parent / note_path.name.replace(".md", ".html")
    body_wrapped = site_header() + body + site_footer()
    return html_page(title, body_wrapped), out_path


def strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


# -----------------------------------------------------------------------------
# 主页 — 两栏：左侧最新 digest + notes（card grid），右侧 stats + tags + 配置
# -----------------------------------------------------------------------------
def render_index() -> str:
    digest_dates = sorted([d.name for d in DIGESTS.iterdir() if d.is_dir()], reverse=True)[:30]
    notes = sorted(NOTES.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:8]
    n_papers_total = sum(
        len(list((DIGESTS / d / "papers").glob("*.md")))
        for d in DIGESTS.iterdir() if d.is_dir() and (DIGESTS / d / "papers").exists()
    )

    # Main column: recent digest cards (clickable) + notes cards
    main_html = f"""
<header class="content-header">
  <div class="date-badge">📅 Today</div>
  <h1 class="page-title">{SITE_TITLE}</h1>
  <p class="lead">{SITE_DESC}</p>
  <div class="quick-stats">
    <span class="stat"><strong>{len(digest_dates)}</strong> days</span>
    <span class="stat"><strong>{n_papers_total}</strong> papers indexed</span>
    <span class="stat"><strong>{len(notes)}</strong> deep notes</span>
    <span class="stat"><strong>EN+ZH</strong> bilingual</span>
  </div>
</header>

<section class="section">
  <h2 class="section-title">📅 Latest Digests</h2>
  <div class="card-grid">
"""

    for date_str in digest_dates[:8]:
        n_papers = len(list((DIGESTS / date_str / "papers").glob("*.md"))) if (DIGESTS / date_str / "papers").exists() else 0
        main_html += f"""
    <a class="day-card" href="digest/{date_str}.html">
      <div class="day-date">{date_str}</div>
      <div class="day-count">{n_papers} papers</div>
      <div class="day-arrow">→</div>
    </a>
"""
    main_html += """
  </div>
</section>

<section class="section">
  <h2 class="section-title">📄 Latest Notes</h2>
  <div class="card-grid">
"""
    for note_path in notes:
        try:
            md = frontmatter.load(note_path)
            t = md.metadata.get("title") or note_path.stem
            v = md.metadata.get("venue", "")
            arxiv = md.metadata.get("arxiv_id", "")
            rel = note_path.relative_to(NOTES)
            html_path = f"notes/{rel.parent}/{note_path.stem}.html"
            venue_short = v.split()[0] if v else ""
            main_html += f"""
    <a class="day-card note-card" href="{html_path}">
      <div class="day-date">{escape_html(t)}</div>
      <div class="day-count">{escape_html(venue_short)} · {escape_html(arxiv)}</div>
      <div class="day-arrow">→</div>
    </a>
"""
        except Exception:
            continue
    main_html += """
  </div>
</section>
"""

    # Sidebar: stats + tags + about
    sidebar_html = f"""
<nav class="sidebar-toc">
  <div class="sidebar-section">
    <div class="sidebar-title">⚙️ 配置</div>
    <p class="muted">主方向：<code>LLM as attacker 的静态防御</code></p>
    <p class="muted">每日 10 篇主方向 + 10 篇大方向</p>
    <p class="muted">修改：编辑 <code>~/.hermes/profile_user.json</code></p>
  </div>

  <div class="sidebar-section">
    <div class="sidebar-title">🏷️ Tags</div>
    <div class="tag-cloud">
      <span class="tag">llm-jailbreak</span>
      <span class="tag">llm-prompt-injection</span>
      <span class="tag">llm-privacy</span>
      <span class="tag">llm-backdoor</span>
      <span class="tag">llm-alignment</span>
      <span class="tag">llm-agent-security</span>
      <span class="tag tag-jailbreak">static-defense</span>
      <span class="tag tag-jailbreak">prompt-injection-defense</span>
      <span class="tag tag-jailbreak">jailbreak-defense</span>
    </div>
  </div>

  <div class="sidebar-section">
    <div class="sidebar-title">📊 数据源</div>
    <p class="muted">· arxiv <code>co:</code> 顶会标注</p>
    <p class="muted">· 关键词检索（jailbreak / privacy / ...）</p>
    <p class="muted">· Semantic Scholar 机构补全</p>
  </div>

  <div class="sidebar-section">
    <div class="sidebar-title">🛠 技术栈</div>
    <p class="muted">· Hermes Agent + cron</p>
    <p class="muted">· arxiv / Semantic Scholar / OpenReview API</p>
    <p class="muted">· KaTeX (LaTeX 公式渲染)</p>
    <p class="muted">· Playwright (顶会 proceedings 抓取)</p>
  </div>
</nav>
"""

    body = f"""
<div class="two-col">
  <aside class="sidebar">
    {sidebar_html}
  </aside>
  <main class="main">
    {main_html}
  </main>
</div>
"""
    body_wrapped = site_header() + body + site_footer()
    return html_page(SITE_TITLE, body_wrapped)


# -----------------------------------------------------------------------------
# Archive — 简单 grid
# -----------------------------------------------------------------------------
def render_archive() -> str:
    digest_dates = sorted([d.name for d in DIGESTS.iterdir() if d.is_dir()], reverse=True)
    items = f"""
<header class="content-header">
  <h1 class="page-title">Archive</h1>
  <p class="lead">所有每日摘要，按日期倒序排列</p>
</header>

<div class="card-grid">
"""
    for date_str in digest_dates:
        n_papers = len(list((DIGESTS / date_str / "papers").glob("*.md"))) if (DIGESTS / date_str / "papers").exists() else 0
        items += f"""
  <a class="day-card" href="digest/{date_str}.html">
    <div class="day-date">{date_str}</div>
    <div class="day-count">{n_papers} papers</div>
    <div class="day-arrow">→</div>
  </a>
"""
    items += "</div>"

    body = f'<div class="single-col">{items}</div>'
    body_wrapped = site_header() + body + site_footer()
    return html_page("Archive", body_wrapped)


def render_rss() -> str:
    digest_dates = sorted([d.name for d in DIGESTS.iterdir() if d.is_dir()], reverse=True)[:20]
    items = ""
    for date_str in digest_dates:
        readme = DIGESTS / date_str / "README.md"
        if not readme.exists():
            continue
        content = readme.read_text(encoding="utf-8")
        first_para = ""
        for line in content.split("\n"):
            if line.strip() and not line.startswith("#") and not line.startswith(">"):
                first_para = line.strip()
                break
        items += f"""    <item>
      <title>{date_str} LLM Security Digest</title>
      <link>{SITE_URL}/digest/{date_str}.html</link>
      <description>{escape_html(first_para[:300])}</description>
      <pubDate>{date_str}T06:00:00Z</pubDate>
      <guid>{SITE_URL}/digest/{date_str}.html</guid>
    </item>
"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{SITE_TITLE}</title>
    <link>{SITE_URL}</link>
    <description>{escape_html(SITE_DESC)}</description>
    <language>zh-CN</language>
    <lastBuildDate>{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}</lastBuildDate>
{items}
  </channel>
</rss>
"""


def build() -> int:
    (DOCS / "digest").mkdir(parents=True, exist_ok=True)
    (DOCS / "notes").mkdir(parents=True, exist_ok=True)
    (DOCS / "tags").mkdir(parents=True, exist_ok=True)
    (DOCS / "assets").mkdir(parents=True, exist_ok=True)

    print("[build] digest pages...")
    n_d = 0
    for d in sorted(DIGESTS.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        readme = d / "README.md"
        if not readme.exists():
            continue
        (DOCS / "digest" / f"{d.name}.html").write_text(render_digest_page(d.name, readme), encoding="utf-8")
        n_d += 1
    print(f"[build] {n_d} digest pages")

    print("[build] note pages...")
    n_n = 0
    for note_path in NOTES.rglob("*.md"):
        try:
            html, out_path = render_note_page(note_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(html, encoding="utf-8")
            src = note_path.parent / "figures"
            if src.exists():
                dst = out_path.parent / "figures"
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            n_n += 1
        except Exception as e:
            print(f"  warn: {note_path}: {e}", file=sys.stderr)
    print(f"[build] {n_n} note pages")

    print("[build] index/archive/rss...")
    (DOCS / "index.html").write_text(render_index(), encoding="utf-8")
    (DOCS / "archive.html").write_text(render_archive(), encoding="utf-8")
    (DOCS / "rss.xml").write_text(render_rss(), encoding="utf-8")
    print(f"[build] done → {DOCS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())