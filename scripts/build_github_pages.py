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

import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

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
DAILY = REPO / "DAILY.md"

BUCKETS = ("main_track", "others")

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
    manifest = load_manifest(readme_path.parent / "manifest.json", required=True)
    assign_manifest_buckets(papers, manifest, load_fact_ids(readme_path.parent / "facts.json"))
    attach_card_figures(papers)
    # A daily manifest freezes the track label for historical pages.  Fall
    # back to the current user-owned statement only for older manifests.
    main_track_label = str(manifest.get("main_track", "")).strip() or read_main_track_label()

    # Sidebar: TOC + category jump + paper jump
    sidebar_html = render_digest_sidebar(papers, date_str, main_track_label)

    # Hero + paper cards
    main_html = render_digest_main(papers, date_str, main_track_label)

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
    match = re.search(
        rf"^\*\*{label}\*\*[：:][ \t]*(.*?)(?={stop}|\Z)",
        body,
        re.DOTALL | re.MULTILINE,
    )
    return match.group(1).strip() if match else ""


def _marked_fields(body: str) -> list[tuple[str, str]]:
    """Read block fields without treating inline Markdown as a new field."""
    return re.findall(
        r"(?ms)^\*\*(?P<label>[^*\n]+)\*\*[：:]\s*"
        r"(?P<value>.*?)(?=^\*\*[^*\n]+\*\*[：:]|\Z)",
        body,
    )


def _analysis_field(body: str, labels: tuple[str, ...]) -> str:
    """Return a complete Chinese analysis block by its bilingual heading."""
    aliases = tuple(re.sub(r"\s+", " ", label).strip().casefold() for label in labels)
    for label, value in _marked_fields(body):
        normalized = re.sub(r"\s+", " ", label).strip().casefold()
        if any(
            normalized == alias
            or normalized.startswith(f"{alias} ")
            or normalized.startswith(f"{alias}/")
            or normalized.startswith(f"{alias} /")
            or normalized.startswith(f"{alias}(")
            or normalized.startswith(f"{alias}（")
            for alias in aliases
        ):
            return value.strip()
    return ""


def _marked_field_prefix(body: str, prefixes: tuple[str, ...]) -> str:
    """Read a complete marked field whose label starts with one of prefixes."""
    normalized_prefixes = tuple(
        re.sub(r"\s+", " ", prefix).strip().casefold() for prefix in prefixes
    )
    for label, value in _marked_fields(body):
        normalized = re.sub(r"\s+", " ", label).strip().casefold()
        if any(normalized.startswith(prefix) for prefix in normalized_prefixes):
            return value.strip()
    return ""


def _unquote_abstract(value: str) -> str:
    """Remove only the blockquote prefix added by the Markdown renderer."""
    text = value.strip()
    lines = text.split("\n")
    if not lines or not lines[0].startswith(">"):
        return text
    return "\n".join(
        line[2:] if line.startswith("> ") else line[1:] if line == ">" else line
        for line in lines
    ).strip()


def _bilingual_value(body: str, label: str, lang: str = "ZH") -> str:
    generated = _analysis_field(body, (label,))
    if generated:
        return generated
    block = _field(body, rf"{label} \(原文 \+ 中文\)")
    match = re.search(rf"- {lang}:\s*(.+?)(?=\n- (?:EN|ZH):|\Z)", block, re.DOTALL)
    return match.group(1).strip() if match else ""


def load_manifest(path: Path, *, required: bool = False) -> dict:
    """Load the immutable daily manifest used for paper bucket assignment."""
    if not path.is_file():
        if required:
            raise ValueError(f"missing daily manifest: {path}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        if required:
            raise ValueError(f"invalid daily manifest: {path}")
        return {}
    if not isinstance(data, dict):
        if required:
            raise ValueError(f"invalid daily manifest: {path}")
        return {}
    return data


def load_fact_ids(path: Path) -> list[str]:
    """Read frozen paper IDs so formal-venue cards match manifest decisions."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_papers = data.get("papers") if isinstance(data, dict) else None
    if not isinstance(raw_papers, list):
        return []
    return [
        str(item.get("paper_id", "")).strip() if isinstance(item, dict) else ""
        for item in raw_papers
    ]


def read_main_track_label() -> str:
    """Read the user-owned current track name from DAILY.md §1.1."""
    try:
        content = DAILY.read_text(encoding="utf-8")
    except OSError:
        return "Main Track"
    match = re.search(
        r"\*\*Current main track \(phase-dependent\):\*\*\s*`([^`]+)`",
        content,
    )
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else "Main Track"


def _paper_id_from_url(url: str) -> str:
    cleaned = url.strip().rstrip(".,")
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#]+)", cleaned, re.IGNORECASE)
    if match:
        arxiv_id = re.sub(r"\.pdf$", "", match.group(1).rstrip("/."), flags=re.IGNORECASE)
        arxiv_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
        return f"arxiv:{arxiv_id}"
    doi_match = re.search(
        r"(?:doi\.org/|/doi/)(10\.\d{4,9}/[^\s?#]+)", cleaned, re.IGNORECASE
    )
    if doi_match:
        return f"doi:{unquote(doi_match.group(1)).rstrip('.,/').lower()}"
    openreview_match = re.search(
        r"openreview\.net/(?:forum|pdf)\?id=([^&#]+)", cleaned, re.IGNORECASE
    )
    if openreview_match:
        return f"openreview:{unquote(openreview_match.group(1))}"
    return cleaned


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
            # Normal materialize manifests use the legacy track names. They
            # are the pipeline's core/broad quota, not display categories.
            track = str(decision.get("track", "")).strip().lower().replace("-", "_")
            bucket = {"core": "main_track", "broad": "others"}.get(track, "others")
        normalized = "main_track" if bucket == "main_track" else "others"
        for paper_id in (decision_key, decision.get("paper_id")):
            if paper_id:
                assignments[str(paper_id).strip()] = normalized
    return assignments


def assign_manifest_buckets(papers: list[dict], manifest: dict, fact_ids: list[str] | None = None) -> None:
    """Attach only the two manifest buckets; unmatched papers stay in Others."""
    assignments = _manifest_bucket_map(manifest)
    fact_ids = fact_ids or []
    for index, paper in enumerate(papers):
        candidate_ids = [paper.get("paper_id", "")]
        if index < len(fact_ids):
            candidate_ids.append(fact_ids[index])
        paper["bucket"] = next(
            (assignments[paper_id] for paper_id in candidate_ids if paper_id in assignments),
            "others",
        )


def bucket_label(bucket: str, main_track_label: str, *, detailed: bool = True) -> str:
    if bucket == "main_track":
        return main_track_label if detailed else "Main Track"
    return "Others"


def bucket_anchor(bucket: str) -> str:
    return f"cat-{bucket.replace('_', '-')}"


def parse_papers_from_md(content: str) -> list[dict]:
    """Parse the display fields used by the daily paper cards."""
    papers = []
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
            "bucket": "others",
            "paper_id": "",
            "abstract_en": "",
            "abstract_zh": "",
            "authors": "",
            "source": "",
            "url": "",
            "problem": "",
            "contribution": "",
            "method": "",
            "result": "",
        }

        # Category from "**会议/来源**" or "**分类**：..."
        m_cat = re.search(r"\*\*分类\*\*[：:]\s*(.+)", body)
        if m_cat:
            paper["category"] = m_cat.group(1).strip()

        # Find complete abstract blocks, including the migrated ZH heading.
        paper["abstract_en"] = _unquote_abstract(
            _marked_field_prefix(body, ("Abstract (EN",))
        )
        paper["abstract_zh"] = _marked_field_prefix(
            body, ("摘要 (中文", "Abstract (ZH")
        )

        paper["authors"] = _field(body, "作者", r"\n\*\*")
        paper["source"] = _field(body, "会议/来源", r"\n\*\*")
        link_line = _field(body, "链接", r"\n\*\*")
        markdown_urls = re.findall(r"\]\((https?://[^)]+)\)", link_line)
        paper["url"] = markdown_urls[0] if markdown_urls else (
            re.search(r"https?://\S+", link_line).group(0) if re.search(r"https?://\S+", link_line) else ""
        )
        paper["paper_id"] = _paper_id_from_url(paper["url"])
        paper["problem"] = _analysis_field(body, ("问题", "Problem")) or _bilingual_value(body, "问题")
        paper["contribution"] = _analysis_field(
            body, ("创新与贡献", "Innovation / Contribution", "贡献", "Innovation")
        ) or _bilingual_value(body, "贡献")
        paper["method"] = _analysis_field(body, ("技术细节", "Technical details", "方法")) or _bilingual_value(body, "方法")
        paper["result"] = _analysis_field(
            body, ("实验结果", "Experiment results", "结果")
        ) or _bilingual_value(body, "结果")

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


def group_papers(papers: list[dict]) -> dict[str, list[dict]]:
    grouped = {bucket: [] for bucket in BUCKETS}
    for paper in papers:
        bucket = paper.get("bucket", "others")
        grouped["main_track" if bucket == "main_track" else "others"].append(paper)
    return grouped


def render_digest_sidebar(papers: list[dict], date_str: str, main_track_label: str) -> str:
    grouped = group_papers(papers)

    cat_html = '<div class="sidebar-section"><div class="sidebar-title">分组</div><ul class="sidebar-cat-list">'
    for bucket in BUCKETS:
        label = bucket_label(bucket, main_track_label)
        short_label = bucket_label(bucket, main_track_label, detailed=False)
        suffix = f" · {escape_html(label)}" if bucket == "main_track" and label != short_label else ""
        cat_html += f'<li><a class="cat-pill" href="#{bucket_anchor(bucket)}"><span>{short_label}</span>{suffix}</a> <span class="muted">({len(grouped[bucket])})</span></li>'
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


def render_digest_main(papers: list[dict], date_str: str, main_track_label: str) -> str:
    grouped = group_papers(papers)

    human_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y 年 %m 月 %d 日")
    html = f"""
<header class="digest-hero">
  <div class="eyebrow"><span class="live-dot"></span> DAILY PAPER · {escape_html(human_date)}</div>
  <h1>Daily Papers</h1>
  <p class="lead">{len(papers)} 篇 · {len(BUCKETS)} 个分组 · 中英文摘要与核心结论</p>
  <div class="quick-stats" aria-label="本期统计">
    <span class="stat"><strong>{len(papers)}</strong><small>篇论文</small></span>
    <span class="stat"><strong>{len(BUCKETS)}</strong><small>个分组</small></span>
    <span class="stat"><strong>中 / EN</strong><small>双语摘要</small></span>
  </div>
</header>
<div class="digest-toolbar" role="search">
  <label class="paper-search"><span aria-hidden="true">⌕</span><input type="search" id="paper-search" placeholder="搜索标题、作者或研究内容…" autocomplete="off"></label>
  <div class="view-note">显示 <strong id="visible-count">{len(papers)}</strong> 篇</div>
</div>
"""

    for bucket in BUCKETS:
        ps = grouped[bucket]
        anchor = bucket_anchor(bucket)
        heading = bucket_label(bucket, main_track_label)
        kicker = bucket_label(bucket, main_track_label, detailed=False).upper()
        html += f'<section id="{anchor}" class="cat-section" data-group-section><div class="cat-heading"><div><span class="cat-kicker">{kicker}</span><h2>{escape_html(heading)}</h2></div><span class="cat-count">{len(ps):02d}</span></div>'
        for p in ps:
            html += render_paper_card(p)
        html += "</section>"

    return html


def render_paper_card(p: dict) -> str:
    n = p["num"]
    title = escape_html(p["title"])
    abs_en = p.get("abstract_en", "").strip()
    abs_zh = p.get("abstract_zh", "").strip()
    source = escape_html(p.get("source", ""))
    authors = escape_html(p.get("authors", ""))
    url = escape_html(p.get("url", ""))

    en_html = ""
    if abs_en:
        en_html = f'<details class="abs-en"><summary><span>English abstract</span><span class="details-hint">展开原文 ＋</span></summary><blockquote>{escape_html(abs_en)}</blockquote></details>'

    zh_html = ""
    if abs_zh:
        zh_html = f'<details class="abs-zh"><summary><span>中文摘要</span><span class="details-hint">展开摘要 ＋</span></summary><p>{escape_html(abs_zh)}</p></details>'
    elif abs_en:
        zh_html = '<div class="abstract-missing muted">（中文摘要未生成）</div>'

    insight_sections = []
    for label, key in (
        ("Problem / 问题", "problem"),
        ("Innovation / Contribution / 创新与贡献", "contribution"),
        ("Technical details / 技术细节", "method"),
        ("Experiment results / 实验结果", "result"),
    ):
        value = p.get(key, "").strip() or "（暂无内容）"
        insight_sections.append(
            f'<details class="paper-detail"><summary><span>{label}</span><span class="details-hint">展开</span></summary><p>{escape_html(value)}</p></details>'
        )
    insight_html = f'<div class="paper-insights">{"".join(insight_sections)}</div>'
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

    search_text = " ".join(
        p.get(key, "")
        for key in ("title", "authors", "abstract_zh", "problem", "contribution", "method", "result")
    ).lower()

    return f"""
<article id="paper-{n}" class="paper-card" data-paper-search="{escape_html(search_text)}">
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
            latest_manifest_path = latest_readme.parent / "manifest.json"
            assign_manifest_buckets(
                latest_papers,
                load_manifest(latest_manifest_path, required=True),
                load_fact_ids(latest_readme.parent / "facts.json"),
            )

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
        preview_label = bucket_label(paper.get("bucket", "others"), "", detailed=False)
        main_html += f"""
        <a class="paper-preview" href="{latest_href}#paper-{paper['num']}">
          <span class="preview-num">{paper['num']:02d}</span>
          <span class="preview-copy"><span class="preview-topic">{preview_label}</span><strong>{escape_html(paper['title'])}</strong><small>{escape_html(summary)}</small></span>
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
