#!/usr/bin/env python3
"""LLM Security Digest — GitHub Pages static site generator.

布局采用文档阅读式双栏结构：
- 左侧 sticky 侧边栏（TOC + 论文跳转）
- 右侧主内容区（论文卡片）
- header 极简 + 字体清晰
- KaTeX 渲染 LaTeX
- 响应式（移动端折叠侧边栏）
"""
from __future__ import annotations

import re
import shutil
import sys
import time
from datetime import datetime
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

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
DIGESTS = REPO / "digests"
NOTES = REPO / "notes"

SITE_TITLE = "LLM Security Digest"
SITE_DESC = "Daily LLM Security papers — top venue & arxiv, bilingual EN/ZH summaries"
SITE_URL = "https://EnlZhao.github.io/LLMSecurityDigest"

FONT_HEAD = """<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fontsource-variable/jetbrains-mono@5.2.5/index.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/lxgw-wenkai-screen-webfont@1.7.0/lxgwwenkaiscreen.css">"""

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


def html_page(
    title: str,
    body: str,
    description: str = SITE_DESC,
    body_class: str = "",
    prefix: str = "",
) -> str:
    full_title = SITE_TITLE if title == SITE_TITLE else f"{title} — {SITE_TITLE}"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{full_title}</title>
  <meta name="description" content="{description}">
  <meta name="theme-color" content="#f7f8f6">
{FONT_HEAD}
  <link rel="stylesheet" href="{prefix}assets/style.css">
  {KATEX_HEAD}
</head>
<body class="{body_class}">
<div class="reading-progress" aria-hidden="true"><span></span></div>
{body}
<script src="{prefix}assets/site.js"></script>
</body>
</html>
"""


def site_header(active: str = "home", prefix: str = "") -> str:
    return f"""
<header class="site-header">
  <div class="header-inner">
    <a href="{prefix}index.html" class="brand" aria-label="LLM Security Digest 首页">
      <span class="brand-mark" aria-hidden="true">L</span>
      <span class="brand-copy"><span class="brand-text">LLM Security</span><span class="brand-sub">Research Digest</span></span>
    </a>
    <button class="nav-toggle" type="button" aria-label="打开导航" aria-expanded="false"><span></span><span></span></button>
    <nav class="primary-nav" aria-label="主导航">
      <a href="{prefix}index.html" class="{'active' if active == 'home' else ''}">首页</a>
      <a href="{prefix}archive.html" class="{'active' if active in ('archive', 'daily') else ''}">Daily Papers</a>
      <a href="{prefix}index.html#notes" class="{'active' if active == 'notes' else ''}">精读笔记</a>
      <a href="{prefix}rss.xml">RSS</a>
      <a class="nav-github" href="https://github.com/EnlZhao/LLMSecurityDigest" target="_blank" rel="noreferrer">GitHub ↗</a>
    </nav>
    <button class="theme-toggle" type="button" aria-label="切换深浅色主题" title="切换主题"><span class="theme-icon">◐</span></button>
  </div>
</header>
"""


def site_footer(prefix: str = "") -> str:
    return f"""
<footer class="site-footer">
  <div class="footer-inner">
    <div class="footer-col">
      <a href="{prefix}index.html" class="footer-brand">LLM Security Digest</a>
      <p class="muted">LLM Security 论文日报与精读笔记。</p>
    </div>
    <div class="footer-col">
      <strong>浏览</strong>
      <p><a href="{prefix}index.html">首页</a><a href="{prefix}archive.html">Daily Papers</a><a href="{prefix}index.html#notes">精读笔记</a></p>
    </div>
    <div class="footer-col">
      <strong>关注方向</strong>
      <p class="muted">Jailbreak · Prompt Injection · Privacy · Agent Security</p>
    </div>
    <div class="footer-col footer-meta"><span>Updated daily</span><a href="{prefix}rss.xml">订阅 RSS ↗</a></div>
  </div>
</footer>
"""


# -----------------------------------------------------------------------------
# 摘要页（每日最多 10 篇）— 两栏：左侧 sidebar（TOC + 分类跳转），右侧论文卡片
# -----------------------------------------------------------------------------
def render_digest_page(date_str: str, readme_path: Path) -> str:
    md = frontmatter.load(readme_path)
    body_html = md_to_html(md.content)
    papers = parse_papers_from_md(md.content)
    attach_card_figures(papers)

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
    body_wrapped = site_header("daily", "../") + body + site_footer("../")
    return html_page(
        date_str,
        body_wrapped,
        description=f"LLM Security daily digest for {date_str}",
        body_class="digest-page",
        prefix="../",
    )


def _field(body: str, label: str, stop: str = r"\n\*\*") -> str:
    match = re.search(rf"\*\*{label}\*\*[：:]\s*(.+?)(?={stop}|\Z)", body, re.DOTALL)
    return match.group(1).strip() if match else ""


def _bilingual_value(body: str, label: str, lang: str = "ZH") -> str:
    generated = _field(body, rf"{label}（LLM 解读）")
    if generated:
        return generated
    block = _field(body, rf"{label} \(原文 \+ 中文\)")
    match = re.search(rf"- {lang}:\s*(.+?)(?=\n- (?:EN|ZH):|\Z)", block, re.DOTALL)
    return match.group(1).strip() if match else ""


def parse_papers_from_md(content: str) -> list[dict]:
    """Parse the display fields used by the daily paper cards."""
    papers = []
    category_by_num: dict[int, str] = {}
    for cat_match in re.finditer(r"^- \*\*[A-Z]\.\s*(.+?)\*\*[：:]\s*(.+)$", content, re.MULTILINE):
        category_name, paper_refs = cat_match.groups()
        for number in re.findall(r"#(\d+)", paper_refs):
            category_by_num[int(number)] = category_name.strip()
    # Each paper starts with ### [N].
    pattern = re.compile(
        r"### \[(\d+)\]\.\s*(.+?)\n(.*?)(?=\n### \[|\n## |$)", re.DOTALL
    )
    for m in pattern.finditer(content):
        num = int(m.group(1))
        title = m.group(2).strip()
        body = m.group(3)

        paper = {
            "num": num,
            "title": title,
            "category": "",
            "abstract_en": "",
            "abstract_zh": "",
            "authors": "",
            "source": "",
            "url": "",
            "problem": "",
            "method": "",
            "result": "",
        }

        # Category from "**会议/来源**" or "**分类**：..."
        m_cat = re.search(r"\*\*分类\*\*[：:]\s*(.+)", body)
        if m_cat:
            paper["category"] = m_cat.group(1).strip()
        if num in category_by_num:
            paper["category"] = category_by_num[num]

        # Find Abstract block
        m_abs = re.search(r"\*\*Abstract \(EN[^\)]*\)\*\*[：:]\s*(.+?)(?=\n\*\*摘要 \(中文\)|\n\*\*问题|\Z)", body, re.DOTALL)
        if m_abs:
            paper["abstract_en"] = m_abs.group(1).strip().strip(">").strip()
        m_abs_zh = re.search(r"\*\*摘要 \(中文\)\*\*[：:]\s*(.+?)(?=\n\*\*问题|\Z)", body, re.DOTALL)
        if m_abs_zh:
            paper["abstract_zh"] = m_abs_zh.group(1).strip()

        paper["authors"] = _field(body, "作者", r"\n\*\*")
        paper["source"] = _field(body, "会议/来源", r"\n\*\*")
        link_line = _field(body, "链接", r"\n\*\*")
        markdown_urls = re.findall(r"\]\((https?://[^)]+)\)", link_line)
        paper["url"] = markdown_urls[0] if markdown_urls else (
            re.search(r"https?://\S+", link_line).group(0) if re.search(r"https?://\S+", link_line) else ""
        )
        paper["problem"] = _bilingual_value(body, "问题")
        paper["method"] = _bilingual_value(body, "方法")
        paper["result"] = _bilingual_value(body, "结果")

        papers.append(paper)
    return papers


def attach_card_figures(papers: list[dict]) -> None:
    """Attach an optional system/framework figure from a matching paper note.

    Images are kept as data URLs in the generated card and are only requested
    after the reader clicks the preview, so daily pages do not decode every
    full-size paper figure on initial load.
    """
    figure_terms = re.compile(
        r"architecture|system(?: overview)?|framework|pipeline|workflow|overview|"
        r"系统架构|系统图|架构图|框架图|方法总览|流程图",
        re.IGNORECASE,
    )
    figures_by_arxiv: dict[str, dict[str, str]] = {}
    for note_path in NOTES.rglob("*.md"):
        note = frontmatter.load(note_path)
        arxiv_id = str(note.metadata.get("arxiv_id", "")).strip()
        if not arxiv_id:
            continue

        explicit = str(note.metadata.get("card_image", "")).strip()
        alt = str(note.metadata.get("card_image_alt", "System architecture")).strip()
        image_path = explicit
        if not image_path:
            for match in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", note.content):
                candidate_alt, candidate_path = match.groups()
                if figure_terms.search(f"{candidate_alt} {candidate_path}"):
                    alt, image_path = candidate_alt, candidate_path
                    break
        if not image_path or re.match(r"https?://", image_path):
            continue

        source = (note_path.parent / image_path).resolve()
        if not source.is_file() or NOTES.resolve() not in source.parents:
            continue
        docs_rel = (Path("notes") / source.relative_to(NOTES)).as_posix()
        figures_by_arxiv[arxiv_id] = {
            "src": f"../{docs_rel}",
            "alt": alt or "System architecture",
        }

    for paper in papers:
        match = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9.]+)", paper.get("url", ""), re.IGNORECASE)
        if match and match.group(1).rstrip(".") in figures_by_arxiv:
            paper["card_figure"] = figures_by_arxiv[match.group(1).rstrip(".")]


def render_digest_sidebar(papers: list[dict], date_str: str) -> str:
    # Group by category
    by_cat = {}
    for p in papers:
        cat = p["category"] or "Other"
        by_cat.setdefault(cat, []).append(p)

    cat_html = '<div class="sidebar-section"><div class="sidebar-title">研究方向</div><ul class="sidebar-cat-list">'
    for cat, ps in by_cat.items():
        anchor = slugify(cat)
        tag_cls = category_tag_class(cat)
        cat_html += f'<li><a class="cat-pill tag-{tag_cls}" href="#cat-{anchor}">{cat}</a> <span class="muted">({len(ps)})</span></li>'
    cat_html += "</ul></div>"

    # Paper list
    paper_html = f'<div class="sidebar-section sidebar-paper-section"><div class="sidebar-title">本期 {len(papers)} 篇</div><ul class="sidebar-paper-list">'
    for p in papers:
        title_short = p["title"][:50] + ("…" if len(p["title"]) > 50 else "")
        paper_html += f'<li><a href="#paper-{p["num"]}"><span class="num">#{p["num"]}</span> {escape_html(title_short)}</a></li>'
    paper_html += "</ul></div>"

    return f"""
<nav class="sidebar-toc">
  <div class="sidebar-section sidebar-date">
    <a class="back-link" href="../archive.html">← 全部日报</a>
    <div class="sidebar-date-day">{date_str[-2:]}</div>
    <div class="sidebar-date-copy"><strong>{date_str}</strong><span>{len(papers)} 篇精选论文</span></div>
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

    human_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y 年 %m 月 %d 日")
    html = f"""
<header class="digest-hero">
  <div class="eyebrow"><span class="live-dot"></span> DAILY PAPER · {escape_html(human_date)}</div>
  <h1>Daily Papers</h1>
  <p class="lead">{len(papers)} 篇 · {len(by_cat)} 个研究方向 · 中英文摘要与核心结论</p>
  <div class="quick-stats" aria-label="本期统计">
    <span class="stat"><strong>{len(papers)}</strong><small>篇论文</small></span>
    <span class="stat"><strong>{len(by_cat)}</strong><small>个方向</small></span>
    <span class="stat"><strong>中 / EN</strong><small>双语摘要</small></span>
  </div>
</header>
<div class="digest-toolbar" role="search">
  <label class="paper-search"><span aria-hidden="true">⌕</span><input type="search" id="paper-search" placeholder="搜索标题、作者或研究内容…" autocomplete="off"></label>
  <div class="view-note">显示 <strong id="visible-count">{len(papers)}</strong> 篇</div>
</div>
"""

    for cat, ps in by_cat.items():
        anchor = slugify(cat)
        tag_cls = category_tag_class(cat)
        html += f'<section id="cat-{anchor}" class="cat-section" data-category-section><div class="cat-heading"><div><span class="cat-kicker">TOPIC</span><h2>{escape_html(cat)}</h2></div><span class="cat-count">{len(ps):02d}</span></div>'
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
    source = escape_html(p.get("source", ""))
    authors = escape_html(p.get("authors", ""))
    url = escape_html(p.get("url", ""))

    en_html = ""
    if abs_en:
        # Strip blockquote markers
        en_text = abs_en.replace("> ", "").strip()
        en_text = re.sub(r"\n\s*\n", "\n\n", en_text)
        en_html = f'<details class="abs-en"><summary><span>English abstract</span><span class="details-hint">展开原文 ＋</span></summary><blockquote>{escape_html(en_text)}</blockquote></details>'

    zh_html = ""
    if abs_zh:
        zh_html = f'<details class="abs-zh"><summary><span>中文摘要</span><span class="details-hint">展开摘要 ＋</span></summary><p>{escape_html(abs_zh)}</p></details>'
    elif abs_en:
        zh_html = '<div class="abstract-missing muted">（中文摘要未生成）</div>'

    insights = []
    for label, key in (("研究问题", "problem"), ("核心方法", "method"), ("主要结果", "result")):
        if p.get(key):
            insights.append(f'<div class="insight-item"><span>{label}</span><p>{escape_html(p[key])}</p></div>')
    insight_html = f'<div class="insight-grid">{"".join(insights)}</div>' if insights else ""
    source_html = f'<span class="venue-chip">{source}</span>' if source else ""
    author_html = f'<span class="authors">{authors}</span>' if authors else ""
    external_html = f'<a class="paper-action primary" href="{url}" target="_blank" rel="noreferrer">阅读原文 <span>↗</span></a>' if url else ""
    figure_html = ""
    if p.get("card_figure"):
        figure = p["card_figure"]
        figure_html = (
            '<button class="figure-trigger" type="button" '
            f'data-figure-src="{escape_html(figure["src"])}" '
            f'data-figure-alt="{escape_html(figure["alt"])}">'
            '<span class="figure-schematic" aria-hidden="true"><i></i><i></i><i></i></span>'
            '<span><b>系统图</b><small>点击查看大图，不占用首屏加载</small></span><strong>↗</strong></button>'
        )

    return f"""
<article id="paper-{n}" class="paper-card" data-paper-search="{escape_html((p['title'] + ' ' + p.get('authors','') + ' ' + p.get('abstract_zh','')).lower())}">
  <div class="card-head">
    <span class="paper-num">{n:02d}</span>
    <div class="paper-heading">
      <div class="card-meta">{source_html}<span class="paper-kind">Research paper</span></div>
      <h3 class="paper-title">{title}</h3>
      {author_html}
    </div>
  </div>
  <div class="card-body">
{figure_html}
    {insight_html}
    {zh_html}
    {en_html}
  </div>
  <div class="card-actions">{external_html}<a class="paper-action" href="#paper-{n}">复制定位链接 <span>§</span></a></div>
</article>
"""


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", text.lower()).strip("-")


def escape_html(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#39;"))


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
    rel = note_path.relative_to(NOTES)
    out_path = DOCS / "notes" / rel.parent / note_path.name.replace(".md", ".html")
    prefix = "../" * (len(out_path.relative_to(DOCS).parts) - 1)

    # Extract H2 sections for TOC
    toc_items = []
    for m in re.finditer(r"<h2[^>]*>(.+?)</h2>", content_html):
        text = m.group(1)
        anchor = slugify(strip_tags(text))
        toc_items.append((text, anchor))

    toc_html = '<nav class="sidebar-toc"><div class="sidebar-section"><a class="back-link" href="' + prefix + 'index.html#notes">← 返回精读笔记</a></div><div class="sidebar-section"><div class="sidebar-title">文章目录</div><ul class="sidebar-toc-list">'
    for text, anchor in toc_items:
        toc_html += f'<li><a href="#{anchor}">{escape_html(text)}</a></li>'
    toc_html += "</ul>"

    # Tags in sidebar
    if tags:
        toc_html += '<div class="sidebar-section"><div class="sidebar-title">研究标签</div><div class="tag-cloud">'
        for t in tags:
            if isinstance(t, str):
                toc_html += f'<span class="tag">{escape_html(t)}</span>'
        toc_html += "</div></div>"

    # External links
    toc_html += '<div class="sidebar-section"><div class="sidebar-title">相关链接</div>'
    if arxiv_id:
        toc_html += f'<p><a href="https://arxiv.org/abs/{arxiv_id}" class="link-ext">arXiv:{arxiv_id}</a></p>'
    toc_html += f'<p><a href="{prefix}archive.html" class="link-ext">浏览 Daily Papers</a></p>'
    toc_html += "</div></nav>"

    # Add stable anchor IDs to H2 sections in content.
    def add_h2_anchor(match):
        attrs = re.sub(r'\s+id=(["\']).*?\1', "", match.group(1))
        anchor = slugify(strip_tags(match.group(2)))
        return f'<h2{attrs} id="{anchor}">{match.group(2)}</h2>'

    content_with_anchors = re.sub(
        r"<h2([^>]*)>(.+?)</h2>",
        add_h2_anchor,
        content_html,
    )
    content_with_anchors = re.sub(
        r"<img\s+([^>]*?)\s*/?>",
        lambda m: f'<img {m.group(1)} loading="lazy" decoding="async">',
        content_with_anchors,
    )

    # Meta bar at top of main content
    meta_bar = '<header class="note-hero"><div class="eyebrow">DEEP READING · 精读笔记</div>'
    meta_bar += f'<div class="venue-badge">{escape_html(venue)}</div>' if venue else ""
    meta_bar += f'<h1 class="paper-title-h1">{escape_html(title)}</h1>'
    if authors:
        meta_bar += f'<p class="paper-authors">{", ".join(str(a) for a in authors)}</p>'
    if arxiv_id:
        meta_bar += f'<p class="paper-arxiv"><a href="https://arxiv.org/abs/{arxiv_id}" target="_blank" rel="noreferrer">阅读 arXiv 原文 ↗</a></p>'
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
    body_wrapped = site_header("notes", prefix) + body + site_footer(prefix)
    return html_page(title, body_wrapped, body_class="note-page", prefix=prefix), out_path


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

    latest_date = digest_dates[0] if digest_dates else ""
    latest_papers = []
    if latest_date:
        latest_readme = DIGESTS / latest_date / "README.md"
        if latest_readme.exists():
            latest_papers = parse_papers_from_md(frontmatter.load(latest_readme).content)

    latest_href = f"digest/{latest_date}.html" if latest_date else "archive.html"
    main_html = f"""
<main class="home-main">
  <section class="home-hero index-hero">
    <div class="index-intro">
      <div class="eyebrow">LLM SECURITY DIGEST</div>
      <h1>Daily Papers</h1>
      <p>LLM Security 论文日报、双语摘要与精读笔记。</p>
    </div>
    <a class="today-entry" href="{latest_href}" aria-label="进入 {latest_date} Daily Papers">
      <span class="today-label"><i></i> TODAY</span>
      <span class="today-date">{latest_date}</span>
      <span class="today-meta"><strong>{len(latest_papers)}</strong> 篇论文 <small>中英双语 · 核心总结</small></span>
      <span class="today-action">进入今日日报 <b>→</b></span>
    </a>
  </section>

  <section class="home-section latest-section" id="latest">
    <div class="section-heading">
      <div><span class="section-index">01</span><p class="eyebrow">LATEST DIGEST</p><h2>今日论文速览</h2></div>
      <a class="text-link" href="{latest_href}">查看完整日报 <span>↗</span></a>
    </div>
    <div class="latest-layout">
      <a class="latest-date-card" href="{latest_href}">
        <span class="date-month">{latest_date[:7] if latest_date else 'LATEST'}</span>
        <strong>{latest_date[-2:] if latest_date else '--'}</strong>
        <span class="date-year">{latest_date} · {len(latest_papers)} papers</span>
        <span class="date-cta">开始阅读 <b>→</b></span>
      </a>
      <div class="paper-preview-list">
"""
    for paper in latest_papers[:4]:
        summary = paper.get("abstract_zh", "")
        summary = summary[:110] + ("…" if len(summary) > 110 else "")
        main_html += f"""
        <a class="paper-preview" href="{latest_href}#paper-{paper['num']}">
          <span class="preview-num">{paper['num']:02d}</span>
          <span class="preview-copy"><span class="preview-topic">{escape_html(paper.get('category',''))}</span><strong>{escape_html(paper['title'])}</strong><small>{escape_html(summary)}</small></span>
          <span class="preview-arrow">↗</span>
        </a>
"""
    main_html += """
      </div>
    </div>
  </section>

  <section class="home-section" id="notes">
    <div class="section-heading">
      <div><span class="section-index">02</span><p class="eyebrow">DEEP READING</p><h2>精读笔记</h2></div>
      <p class="section-desc">从“知道这篇论文”到真正理解它的机制、实验与局限。</p>
    </div>
    <div class="notes-grid">
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
      <a class="note-card" href="{html_path}">
        <span class="note-type">PAPER NOTE</span>
        <h3>{escape_html(t)}</h3>
        <p>{escape_html(venue_short)} · arXiv {escape_html(arxiv)}</p>
        <span class="note-read">阅读全文 <b>↗</b></span>
      </a>
"""
        except Exception:
            continue
    main_html += """
    </div>
  </section>

  <section class="topic-band">
    <div><span class="eyebrow">FOCUS AREAS</span><h2>持续追踪六个<br>LLM 安全方向</h2></div>
    <div class="topic-list"><span>Jailbreak</span><span>Prompt Injection</span><span>Privacy</span><span>Backdoor</span><span>Alignment</span><span>Agent Security</span></div>
  </section>
</main>
"""

    body_wrapped = site_header("home") + main_html + site_footer()
    return html_page(SITE_TITLE, body_wrapped, body_class="home-page")


# -----------------------------------------------------------------------------
# Archive — 简单 grid
# -----------------------------------------------------------------------------
def render_archive() -> str:
    digest_dates = sorted([d.name for d in DIGESTS.iterdir() if d.is_dir()], reverse=True)
    items = f"""
<header class="archive-hero">
  <div class="eyebrow">DAILY PAPER ARCHIVE</div>
  <h1>每日论文<br><em>阅读档案</em></h1>
  <p>按日期浏览所有 LLM Security 日报。每期包含中英文摘要与问题—方法—结果速读。</p>
</header>
<div class="archive-toolbar"><span>共 {len(digest_dates)} 期</span><label><span>⌕</span><input id="archive-search" type="search" placeholder="搜索日期…"></label></div>
<div class="archive-list">
"""
    for index, date_str in enumerate(digest_dates, 1):
        n_papers = len(list((DIGESTS / date_str / "papers").glob("*.md"))) if (DIGESTS / date_str / "papers").exists() else 0
        try:
            date_label = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A")
        except ValueError:
            date_label = "Daily digest"
        items += f"""
  <a class="archive-row" href="digest/{date_str}.html" data-archive-date="{date_str}">
    <span class="archive-index">{index:02d}</span>
    <span class="archive-date"><strong>{date_str}</strong><small>{date_label}</small></span>
    <span class="archive-meta"><b>{n_papers}</b> papers</span>
    <span class="archive-tags"><i>中英双语</i><i>核心总结</i></span>
    <span class="archive-arrow">↗</span>
  </a>
"""
    items += "</div>"

    body = f'<main class="archive-main">{items}</main>'
    body_wrapped = site_header("archive") + body + site_footer()
    return html_page("Daily Papers", body_wrapped, body_class="archive-page")


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
