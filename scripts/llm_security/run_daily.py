#!/usr/bin/env python3
"""LLM Security Daily — 候选抓取脚本（数据采集层）

⚠ 本脚本不做质量判断、不写摘要、不打分——只负责从多个数据源抓 raw 候选。
真正的"选 20 篇 + 双语摘要 + 过滤"由 cron job 中的 LLM agent 完成。

数据源：
1. arxiv `co:` 顶会注释（USENIX Security / S&P / CCS / NDSS / NeurIPS / ICML / ICLR / AAAI / ACL / EMNLP）
2. arxiv abs 关键词命中（jailbreak / prompt injection / privacy / backdoor / agent security / alignment）
3. Semantic Scholar 机构补全（best-effort，429 跳过）

用法：
    python run_daily.py                  # 抓候选 → stdout JSON（cron 喂给 agent）
    python run_daily.py --out raw.json    # 写到文件
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable
from urllib import error, parse, request

# -----------------------------------------------------------------------------
# 常量
# -----------------------------------------------------------------------------
ARXIV_API_URL = "https://export.arxiv.org/api/query"
SS_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_NS = {"arxiv": "http://arxiv.org/schemas/atom"}
USER_AGENT = "llm-security-daily/1.0"
NETWORK_TIMEOUT = 30
SS_BATCH_SIZE = 100

# 顶会检索 queries（核心数据源；按"已召开/临近"排序）
TOP_VENUE_QUERIES = [
    # 安全 4 大
    'co:"USENIX Security 2026"',
    'co:"USENIX Security 2025"',
    'co:"IEEE S&P 2026"',
    'co:"IEEE S&P 2025"',
    'co:"Oakland"',
    'co:"CCS 2024"',
    'co:"CCS 2025"',
    'co:"NDSS 2025"',
    'co:"NDSS 2026"',
    # AI 顶会
    'co:"NeurIPS 2025"',
    'co:"NeurIPS 2024"',
    'co:"ICML 2025"',
    'co:"ICLR 2025"',
    'co:"ICLR 2026"',
    'co:"AAAI 2025"',
    'co:"AAAI 2026"',
    'co:"ACL 2025"',
    'co:"EMNLP 2025"',
]

# 关键词兜底 query（每个覆盖一个 LLM Security 子方向）
KEYWORD_QUERIES = [
    'abs:"jailbreak" OR abs:"prompt injection"',
    'abs:"membership inference" OR abs:"model extraction"',
    'abs:"adversarial attack" AND (abs:"language model" OR abs:"LLM")',
    'abs:"backdoor" AND (abs:"language model" OR abs:"LLM")',
    'abs:"alignment" AND abs:"safety" AND abs:"language model"',
    'abs:"agent" AND abs:"security" AND (abs:"LLM" OR abs:"language model")',
    'abs:"red team" AND (abs:"LLM" OR abs:"language model")',
]


@dataclass
class Candidate:
    arxiv_id: str
    title: str
    authors: list[str]
    affiliations: list[str]
    summary: str
    primary_category: str
    comment: str
    published: str
    updated: str
    source_queries: list[str] = field(default_factory=list)
    source: str = "arxiv"  # arxiv | semantic-scholar


# -----------------------------------------------------------------------------
# 抓取层
# -----------------------------------------------------------------------------
def fetch_feed(query: str, max_results: int, retries: int = 2) -> str:
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(max_results),
    }
    url = f"{ARXIV_API_URL}?{parse.urlencode(params)}"
    last_err = None
    for attempt in range(retries):
        try:
            req = request.Request(url, headers={"User-Agent": USER_AGENT})
            with request.urlopen(req, timeout=NETWORK_TIMEOUT) as r:
                return r.read().decode("utf-8")
        except error.HTTPError as e:
            last_err = e
            if e.code == 429:
                wait = (attempt + 1) * 3
                print(f"[fetch] 429, sleeping {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            last_err = e
            time.sleep((attempt + 1) * 2)
    raise last_err if last_err else RuntimeError("fetch failed")


def parse_entries(feed_text: str) -> list[dict]:
    root = ET.fromstring(feed_text)
    entries = []
    for entry in root.findall("atom:entry", ATOM_NS):
        title = " ".join(entry.findtext("atom:title", default="", namespaces=ATOM_NS).split())
        summary = " ".join(entry.findtext("atom:summary", default="", namespaces=ATOM_NS).split())
        arxiv_id_raw = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
        m = re.search(r"arxiv\.org/abs/([\w./\-]+)", arxiv_id_raw)
        if not m:
            continue
        arxiv_id = re.sub(r"v\d+$", "", m.group(1))
        authors = []
        affiliations = []
        for au in entry.findall("atom:author", ATOM_NS):
            name = au.findtext("atom:name", default="", namespaces=ATOM_NS)
            affil = ""
            for prefix in ("arxiv", "atom"):
                affil = au.findtext(f"{prefix}:affiliation", default="",
                                    namespaces={"arxiv": "http://arxiv.org/schemas/atom", "atom": "http://www.w3.org/2005/Atom"})
                if affil:
                    break
            if name:
                authors.append(name)
            if affil:
                affiliations.append(affil)
        primary_cat_el = entry.find("arxiv:primary_category", ARXIV_NS)
        primary_cat = primary_cat_el.get("term") if primary_cat_el is not None else ""
        comment = ""
        for prefix in ("arxiv", "atom"):
            comment = entry.findtext(f"{prefix}:comment", default="",
                                     namespaces={"arxiv": "http://arxiv.org/schemas/atom", "atom": "http://www.w3.org/2005/Atom"})
            if comment:
                break
        published = entry.findtext("atom:published", default="", namespaces=ATOM_NS)
        updated = entry.findtext("atom:updated", default="", namespaces=ATOM_NS)
        entries.append({
            "arxiv_id": arxiv_id,
            "title": title,
            "summary": summary,
            "authors": authors,
            "affiliations": affiliations,
            "primary_category": primary_cat,
            "comment": comment,
            "published": published,
            "updated": updated,
        })
    return entries


def fetch_candidates(per_venue: int = 50, per_keyword: int = 40) -> list[Candidate]:
    """两阶段抓取：顶会（高优）→ 关键词（兜底）。"""
    seen: dict[str, Candidate] = {}
    venue_hits = 0
    kw_hits = 0
    venue_ok = 0
    kw_ok = 0

    print(f"[fetch] stage 1: {len(TOP_VENUE_QUERIES)} top-venue queries", file=sys.stderr)
    for q in TOP_VENUE_QUERIES:
        try:
            entries = parse_entries(fetch_feed(q, per_venue))
            for e in entries:
                if e["arxiv_id"] not in seen:
                    seen[e["arxiv_id"]] = Candidate(**e, source_queries=[q])
                else:
                    seen[e["arxiv_id"]].source_queries.append(q)
                venue_hits += 1
            venue_ok += 1
        except Exception as exc:
            print(f"[fetch] venue skip: {q[:50]} -> {type(exc).__name__}", file=sys.stderr)
            continue
    print(f"[fetch] stage 1: {venue_ok}/{len(TOP_VENUE_QUERIES)} ok, {venue_hits} hits", file=sys.stderr)

    print(f"[fetch] stage 2: {len(KEYWORD_QUERIES)} keyword queries", file=sys.stderr)
    for q in KEYWORD_QUERIES:
        try:
            entries = parse_entries(fetch_feed(q, per_keyword))
            for e in entries:
                if e["arxiv_id"] not in seen:
                    seen[e["arxiv_id"]] = Candidate(**e, source_queries=[q])
                else:
                    seen[e["arxiv_id"]].source_queries.append(q)
                kw_hits += 1
            kw_ok += 1
        except Exception as exc:
            print(f"[fetch] keyword skip: {q[:50]} -> {type(exc).__name__}", file=sys.stderr)
            break  # 429 后不再尝试
    print(f"[fetch] stage 2: {kw_ok}/{len(KEYWORD_QUERIES)} ok, {kw_hits} hits", file=sys.stderr)

    print(f"[fetch] total unique candidates: {len(seen)}", file=sys.stderr)
    return list(seen.values())


def enrich_affiliations(candidates: list[Candidate]) -> int:
    """用 Semantic Scholar 补全 affiliations（仅对空 affiliations 的）。"""
    targets = [c for c in candidates if not c.affiliations and c.arxiv_id]
    if not targets:
        return 0
    enriched = 0
    for i in range(0, len(targets), SS_BATCH_SIZE):
        batch = targets[i:i + SS_BATCH_SIZE]
        ids = [f"ARXIV:{c.arxiv_id}" for c in batch]
        body = json.dumps({"ids": ids}).encode("utf-8")
        url = SS_BATCH_URL + "?fields=authors.name,authors.affiliations"
        req = request.Request(url, data=body,
                              headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
                              method="POST")
        data = None
        for attempt in range(2):
            try:
                with request.urlopen(req, timeout=NETWORK_TIMEOUT) as r:
                    data = json.loads(r.read())
                break
            except error.HTTPError as exc:
                if exc.code == 429:
                    wait = 3 * (attempt + 1)
                    print(f"[ss] 429, sleeping {wait}s...", file=sys.stderr)
                    time.sleep(wait)
                    continue
                print(f"[ss] HTTPError: {exc}", file=sys.stderr)
                break
            except Exception as exc:
                print(f"[ss] error: {exc}", file=sys.stderr)
                break
        if not data:
            continue
        # data 是 list，与 batch 对应
        for j, paper in enumerate(data):
            if not isinstance(paper, dict):
                continue
            c = batch[j]
            new_affils = []
            for au in paper.get("authors", []) or []:
                for affil in au.get("affiliations", []) or []:
                    if affil and affil not in new_affils:
                        new_affils.append(affil)
            if new_affils:
                c.affiliations = new_affils
                enriched += 1
        time.sleep(1)  # SS 限速保护
    return enriched


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-venue", type=int, default=50)
    parser.add_argument("--per-keyword", type=int, default=40)
    parser.add_argument("--out", type=Path, default=None, help="写 JSON 到文件（默认 stdout）")
    parser.add_argument("--skip-ss", action="store_true", help="跳过 Semantic Scholar 补全")
    args = parser.parse_args()

    cands = fetch_candidates(args.per_venue, args.per_keyword)
    if not args.skip_ss:
        n = enrich_affiliations(cands)
        print(f"[ss] enriched {n} candidates with affiliations", file=sys.stderr)

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(cands),
        "candidates": [asdict(c) for c in cands],
    }
    out_text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(out_text, encoding="utf-8")
        print(f"[main] wrote {args.out} ({len(cands)} candidates)", file=sys.stderr)
    else:
        sys.stdout.write(out_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())