#!/usr/bin/env python3
"""Headless paper collection CLI. LLMs may plan/rank, but never write facts."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import parse

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_security_digest.papers.env import load_dotenv
from llm_security_digest.papers.models import SearchPlan, SelectionEntry
from llm_security_digest.papers.pipeline import collect, default_client, materialize, write_json
from llm_security_digest.papers.sources import OPENREVIEW_API_URL


DEFAULT_PLAN = {
    "queries": [
        'abs:"jailbreak" OR abs:"prompt injection"',
        'abs:"backdoor" AND (abs:"LLM" OR abs:"language model")',
        'abs:"agent security" OR abs:"LLM security"',
    ],
    "filter_keywords": [
        "jailbreak", "prompt injection", "backdoor", "language model", "LLM", "agent security"
    ],
    "sources": ["arxiv", "openreview"],
    "openreview_venues": [
        "ICLR.cc/2026/Conference",
        "ICLR.cc/2025/Conference",
        "NeurIPS.cc/2025/Conference",
        "ICML.cc/2026/Conference",
    ],
    "target": 10,
    "scholar_enrich_limit": 30,
}


def _data_dir(value: str | None = None) -> Path:
    configured = value or os.getenv("LLMSD_DATA_DIR")
    return Path(configured).expanduser().resolve() if configured else (REPO / ".data").resolve()


def command_init_plan(args: argparse.Namespace) -> int:
    if args.out.exists() and not args.force:
        print(f"refusing to overwrite existing plan: {args.out}", file=sys.stderr)
        return 2
    write_json(args.out, DEFAULT_PLAN)
    print(f"[plan] wrote {args.out}", file=sys.stderr)
    return 0


def command_collect(args: argparse.Namespace) -> int:
    plan = SearchPlan.load(args.plan)
    payload = collect(plan)
    write_json(args.out, payload)
    print(f"[collect] wrote {payload['total']} candidates to {args.out}", file=sys.stderr)
    for report in payload["source_reports"]:
        print(f"[source] {report}", file=sys.stderr)
    return 0 if payload["total"] else 3


def command_materialize(args: argparse.Namespace) -> int:
    payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    selections = SelectionEntry.load_many(args.selection)
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    target = args.target if args.target is not None else int(plan.get("target", 10))
    scholar_limit = args.scholar_limit if args.scholar_limit is not None else int(plan.get("scholar_enrich_limit", 30))
    facts, manifest = materialize(
        candidates_payload=payload,
        selections=selections,
        data_dir=_data_dir(args.data_dir),
        target=target,
        scholar_limit=scholar_limit,
    )
    write_json(args.facts, facts)
    write_json(args.manifest, manifest)
    print(
        f"[materialize] verified {manifest['published']}/{manifest['target']}; "
        f"rejected {len(manifest['rejected'])}",
        file=sys.stderr,
    )
    return 0 if facts["papers"] else 4


def _load_paper(facts_path: Path, paper_id: str) -> dict:
    payload = json.loads(facts_path.read_text(encoding="utf-8"))
    matches = [paper for paper in payload.get("papers", []) if paper.get("paper_id") == paper_id]
    if len(matches) != 1:
        raise ValueError(f"paper_id not found or ambiguous: {paper_id}")
    return matches[0]


def _content_path(paper: dict, key: str, data_dir: Path) -> Path:
    value = str(paper.get("content", {}).get(key, "")).strip()
    if not value:
        raise ValueError(f"paper content is missing {key}")
    path = Path(value)
    # Facts produced before portable paths were introduced may still contain
    # absolute paths. Keep them readable while all new snapshots stay portable.
    return path if path.is_absolute() else (data_dir / path)


def command_outline(args: argparse.Namespace) -> int:
    paper = _load_paper(args.facts, args.paper_id)
    outline_path = _content_path(paper, "outline_path", _data_dir(args.data_dir))
    sys.stdout.write(outline_path.read_text(encoding="utf-8") + "\n")
    return 0


def _section_text(paper: dict, section_id: str, data_dir: Path) -> str:
    text = _content_path(paper, "text_path", data_dir).read_text(encoding="utf-8")
    outline = json.loads(_content_path(paper, "outline_path", data_dir).read_text(encoding="utf-8"))
    indexes = {section["id"]: index for index, section in enumerate(outline)}
    if section_id not in indexes:
        raise ValueError(f"unknown section: {section_id}")
    index = indexes[section_id]
    section = outline[index]
    if "page" in section:
        marker = f"[[PAGE {section['page']}]]"
        next_marker = f"[[PAGE {section['page'] + 1}]]"
        start = text.find(marker)
        end = text.find(next_marker, start + len(marker))
        return text[start:end if end >= 0 else None].strip()
    start = int(section.get("offset", 0))
    end = int(outline[index + 1].get("offset", len(text))) if index + 1 < len(outline) else len(text)
    return text[start:end].strip()


def command_read_section(args: argparse.Namespace) -> int:
    paper = _load_paper(args.facts, args.paper_id)
    value = _section_text(paper, args.section_id, _data_dir(args.data_dir))
    sys.stdout.write(value[: args.max_chars] + "\n")
    return 0


def command_find(args: argparse.Namespace) -> int:
    paper = _load_paper(args.facts, args.paper_id)
    text = _content_path(paper, "text_path", _data_dir(args.data_dir)).read_text(encoding="utf-8")
    lowered = text.casefold()
    query = args.query.casefold()
    cursor = 0
    matches = []
    while len(matches) < args.limit:
        index = lowered.find(query, cursor)
        if index < 0:
            break
        start = max(index - args.context, 0)
        end = min(index + len(args.query) + args.context, len(text))
        matches.append({"offset": index, "text": text[start:end]})
        cursor = index + len(query)
    sys.stdout.write(json.dumps({"paper_id": args.paper_id, "query": args.query, "matches": matches}, ensure_ascii=False, indent=2) + "\n")
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    client = default_client()
    checks = []
    for name, url in (
        ("arxiv", "https://export.arxiv.org/api/query?search_query=id:2404.01833&max_results=1"),
        ("crossref", "https://api.crossref.org/works/10.1145/3658644.3690227"),
    ):
        try:
            response = client.get(url, max_bytes=5 * 1024 * 1024)
            checks.append({"name": name, "status": "ok", "http_status": response.status})
        except Exception as exc:
            checks.append({"name": name, "status": "error", "error_type": type(exc).__name__})
    openreview_stage = "venue_query"
    try:
        venue_query = parse.urlencode({"content.venueid": "ICLR.cc/2025/Conference", "limit": "1"})
        response = client.get(f"{OPENREVIEW_API_URL}?{venue_query}", max_bytes=5 * 1024 * 1024)
        notes = response.json().get("notes", [])
        if len(notes) != 1 or not notes[0].get("id"):
            raise ValueError("OpenReview venue query returned no paper")
        note_id = str(notes[0]["id"])
        openreview_stage = "bibtex"
        bibtex = client.get(
            f"https://openreview.net/bibtex?id={parse.quote(note_id)}",
            headers={"Accept": "application/x-bibtex"},
            max_bytes=2 * 1024 * 1024,
        ).text().lstrip()
        if not bibtex.startswith("@"):
            raise ValueError("OpenReview BibTeX endpoint returned non-BibTeX content")
        checks.append({"name": "openreview", "status": "ok", "http_status": response.status, "bibtex": "ok"})
    except Exception as exc:
        checks.append({
            "name": "openreview",
            "status": "error",
            "stage": openreview_stage,
            "error_type": type(exc).__name__,
            "http_status": getattr(exc, "code", None),
        })
    checks.append({"name": "serpapi", "status": "configured" if os.getenv("SERPAPI_API_KEY") else "missing"})
    data_dir = _data_dir(args.data_dir)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write-test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        checks.append({"name": "data_dir", "status": "ok", "path": str(data_dir)})
    except Exception as exc:
        checks.append({"name": "data_dir", "status": "error", "error_type": type(exc).__name__})
    sys.stdout.write(json.dumps({"checks": checks}, ensure_ascii=False, indent=2) + "\n")
    return 0 if all(check["status"] in {"ok", "configured"} for check in checks) else 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_plan = subparsers.add_parser("init-plan", help="write a safe search-plan template")
    init_plan.add_argument("--out", type=Path, required=True)
    init_plan.add_argument("--force", action="store_true")
    init_plan.set_defaults(func=command_init_plan)

    collect_parser = subparsers.add_parser("collect", help="collect script-owned paper facts")
    collect_parser.add_argument("--plan", type=Path, required=True)
    collect_parser.add_argument("--out", type=Path, required=True)
    collect_parser.set_defaults(func=command_collect)

    materialize_parser = subparsers.add_parser("materialize", help="verify BibTeX and full text for ranked IDs")
    materialize_parser.add_argument("--candidates", type=Path, required=True)
    materialize_parser.add_argument("--selection", type=Path, required=True)
    materialize_parser.add_argument("--facts", type=Path, required=True)
    materialize_parser.add_argument("--manifest", type=Path, required=True)
    materialize_parser.add_argument("--data-dir")
    materialize_parser.add_argument("--target", type=int)
    materialize_parser.add_argument("--scholar-limit", type=int)
    materialize_parser.set_defaults(func=command_materialize)

    outline = subparsers.add_parser("outline", help="print verified paper outline")
    outline.add_argument("--facts", type=Path, required=True)
    outline.add_argument("--paper-id", required=True)
    outline.add_argument("--data-dir")
    outline.set_defaults(func=command_outline)

    read_section = subparsers.add_parser("read-section", help="read one bounded paper section")
    read_section.add_argument("--facts", type=Path, required=True)
    read_section.add_argument("--paper-id", required=True)
    read_section.add_argument("--section-id", required=True)
    read_section.add_argument("--max-chars", type=int, default=12000)
    read_section.add_argument("--data-dir")
    read_section.set_defaults(func=command_read_section)

    find = subparsers.add_parser("find", help="find bounded passages in verified full text")
    find.add_argument("--facts", type=Path, required=True)
    find.add_argument("--paper-id", required=True)
    find.add_argument("--query", required=True)
    find.add_argument("--limit", type=int, default=5)
    find.add_argument("--context", type=int, default=500)
    find.add_argument("--data-dir")
    find.set_defaults(func=command_find)

    doctor = subparsers.add_parser("doctor", help="check headless server prerequisites")
    doctor.add_argument("--data-dir")
    doctor.set_defaults(func=command_doctor)
    return parser


def main() -> int:
    load_dotenv(REPO / ".env")
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
