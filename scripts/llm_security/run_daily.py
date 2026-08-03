#!/usr/bin/env python3
"""Headless paper collection CLI. LLMs may plan/rank, but never write facts."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_security_digest.papers.env import load_dotenv
from llm_security_digest.papers.models import DEFAULT_OPENREVIEW_VENUES, SearchPlan, SelectionEntry
from llm_security_digest.papers.pipeline import collect, default_client, materialize, write_json
from llm_security_digest.papers.openreview_client import is_openreview_auth_error, openreview_error_message
from llm_security_digest.papers.sources import OpenReviewSource
from llm_security_digest.evolution import EvolutionStore, EvolutionValidationError, apply_overlay, validate_evolution


DEFAULT_PLAN = {
    "queries": [
        'abs:"jailbreak" OR abs:"prompt injection"',
        'abs:"backdoor" AND (abs:"LLM" OR abs:"language model")',
        'abs:"agent security" OR abs:"LLM security"',
    ],
    "filter_keywords": [
        "jailbreak", "prompt injection", "backdoor", "language model", "LLM", "agent security"
    ],
    "sources": ["official", "openreview", "crossref", "ieee_xplore", "arxiv"],
    "openreview_venues": list(DEFAULT_OPENREVIEW_VENUES),
    "target": 10,
    "scholar_enrich_limit": 30,
    "crossref_venues": ["ieee-sp", "acm-ccs", "tdsc", "tifs", "tops"],
    "venue_groups": [
        "usenix-security", "ieee-sp", "acm-ccs", "ndss", "iclr", "neurips",
        "icml", "cvpr", "eccv", "acl", "emnlp", "aaai", "ijcai", "tdsc", "tifs", "tops",
    ],
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
    evolution_store = _evolution_store(getattr(args, "data_dir", None))
    evolution_health = evolution_store.health_check()
    active = evolution_store.load_active()
    plan = apply_overlay(plan, active.get("overlay", {}))
    payload = collect(plan)
    payload["evolution"] = {
        "active_version": active.get("version", "baseline"),
        "health_check": evolution_health,
    }
    write_json(args.out, payload)
    print(f"[collect] wrote {payload['total']} candidates to {args.out}", file=sys.stderr)
    for report in payload["source_reports"]:
        print(f"[source] {report}", file=sys.stderr)
    return 0 if payload["total"] else 3


def command_materialize(args: argparse.Namespace) -> int:
    payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    selections = SelectionEntry.load_many(args.selection)
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    # Keep candidate-provided budgets typed.  Coercing a malformed string
    # here would bypass the materializer's bounded integer contract.
    target = args.target if args.target is not None else plan.get("target", 10)
    scholar_limit = args.scholar_limit if args.scholar_limit is not None else plan.get("scholar_enrich_limit", 30)
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


def _evolution_store(data_dir: str | None) -> EvolutionStore:
    return EvolutionStore(_data_dir(data_dir) / "evolution")


def _load_json_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _load_json_value(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def command_reflect(args: argparse.Namespace) -> int:
    candidate = _load_json_object(args.input) if args.input else {"overlay": {}}
    store = _evolution_store(args.data_dir)
    try:
        path = store.save_candidate(candidate)
    except EvolutionValidationError as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 2
    if args.out:
        write_json(args.out, json.loads(path.read_text(encoding="utf-8")))
    stored = store.load_candidate(path)
    print(json.dumps({"status": "candidate", "version": stored["version"], "path": str(path)}, ensure_ascii=False))
    return 0


def _candidate_from_args(store: EvolutionStore, args: argparse.Namespace) -> dict:
    if args.candidate:
        return store.load_candidate(args.candidate)
    if args.version:
        return store.load_candidate(args.version)
    raise ValueError("--candidate or --version is required")


def command_validate_evolution(args: argparse.Namespace) -> int:
    try:
        candidate = _load_json_object(args.candidate) if args.candidate else _evolution_store(args.data_dir).load_candidate(args.version)
        report = validate_evolution(candidate)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "invalid", "error_type": type(exc).__name__, "error": str(exc)[:300]}))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0


def command_shadow_evolution(args: argparse.Namespace) -> int:
    store = _evolution_store(args.data_dir)
    try:
        candidate = _candidate_from_args(store, args)
        # Shadow fixtures are immutable candidate-owned tests. Keeping the
        # CLI free of an override path prevents an external fixture file from
        # bypassing the candidate's required regression cases.
        report = store.shadow(candidate)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:300]}))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 3


def command_activate_evolution(args: argparse.Namespace) -> int:
    store = _evolution_store(args.data_dir)
    try:
        candidate = _candidate_from_args(store, args)
        if not args.shadow_report:
            raise EvolutionValidationError("--shadow-report is required; activation never runs shadow implicitly")
        report = _load_json_object(args.shadow_report)
        result = store.activate(candidate, report=report)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:300]}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


def command_evolution_status(args: argparse.Namespace) -> int:
    print(json.dumps(_evolution_store(args.data_dir).status(), ensure_ascii=False))
    return 0


def command_rollback_evolution(args: argparse.Namespace) -> int:
    try:
        result = _evolution_store(args.data_dir).rollback(args.version)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:300]}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


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
    openreview_source = OpenReviewSource()
    try:
        checks.append({"name": "openreview", **openreview_source.probe("ICLR.cc/2025/Conference")})
    except Exception as exc:
        failure = openreview_source.errors[-1] if openreview_source.errors else {}
        checks.append({
            "name": "openreview",
            "status": "error",
            "stage": failure.get("stage", "auth" if is_openreview_auth_error(exc) else "venue_query"),
            "error_type": type(exc).__name__,
            "http_status": failure.get("http_status", getattr(exc, "status_code", getattr(exc, "code", None))),
            "message": openreview_error_message(exc),
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
    collect_parser.add_argument("--data-dir")
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

    reflect = subparsers.add_parser("reflect", help="store a validated Hermes evolution candidate")
    reflect.add_argument("--input", type=Path)
    reflect.add_argument("--out", type=Path)
    reflect.add_argument("--data-dir")
    reflect.set_defaults(func=command_reflect)

    validate_evolution_parser = subparsers.add_parser("validate-evolution", help="validate an evolution candidate")
    validate_evolution_parser.add_argument("--candidate", type=Path)
    validate_evolution_parser.add_argument("--version")
    validate_evolution_parser.add_argument("--data-dir")
    validate_evolution_parser.set_defaults(func=command_validate_evolution)

    shadow_evolution = subparsers.add_parser("shadow-evolution", help="run an evolution candidate against fixtures")
    shadow_evolution.add_argument("--candidate", type=Path)
    shadow_evolution.add_argument("--version")
    shadow_evolution.add_argument("--data-dir")
    shadow_evolution.set_defaults(func=command_shadow_evolution)

    activate_evolution = subparsers.add_parser("activate-evolution", help="atomically activate a candidate with a persisted passed shadow report")
    activate_evolution.add_argument("--candidate", type=Path)
    activate_evolution.add_argument("--version")
    activate_evolution.add_argument("--shadow-report", type=Path)
    activate_evolution.add_argument("--data-dir")
    activate_evolution.set_defaults(func=command_activate_evolution)

    evolution_status = subparsers.add_parser("evolution-status", help="show active evolution and history counts")
    evolution_status.add_argument("--data-dir")
    evolution_status.set_defaults(func=command_evolution_status)

    rollback_evolution = subparsers.add_parser("rollback-evolution", help="record an explicit evolution rollback")
    rollback_evolution.add_argument("--version")
    rollback_evolution.add_argument("--data-dir")
    rollback_evolution.set_defaults(func=command_rollback_evolution)

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
