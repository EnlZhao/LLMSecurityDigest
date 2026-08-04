#!/usr/bin/env python3
"""Collect bounded browser evidence for Hermes candidate discovery.

The output is never a candidate facts file.  It contains only allowlisted URL
evidence and must be passed back through the normal Python collectors before a
paper can be materialized.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_security_digest.papers.headless import HeadlessDiscovery, HeadlessDiscoveryError
from llm_security_digest.route_catalog import RouteCatalog


def main() -> int:
    parser = argparse.ArgumentParser(description="collect allowlisted headless browser evidence")
    parser.add_argument("--input", type=Path, required=True, help="JSON request containing only allowlisted URLs")
    parser.add_argument("--out", type=Path, required=True, help="evidence/raw-response JSON output path")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="capture bounded raw HTML/JSON/PDF response bytes and provenance",
    )
    parser.add_argument("--venue", help="registered venue context for route-catalog persistence")
    parser.add_argument("--source", default="official", help="registered route source (used with --venue)")
    parser.add_argument("--adapter", help="registered adapter (used with --venue)")
    parser.add_argument("--route-kind", default="index", help="route kind to persist (used with --venue)")
    parser.add_argument("--evidence-source", default="browser", help="provenance label for persisted route metadata")
    parser.add_argument("--data-dir", type=Path, help="runtime directory containing route_catalog.sqlite3")
    args = parser.parse_args()
    if args.out.name.casefold() == "facts.json":
        print("headless output path cannot be facts.json", file=sys.stderr)
        return 2
    try:
        request = json.loads(args.input.read_text(encoding="utf-8"))
        discovery = HeadlessDiscovery()
        route_context = None
        if args.venue:
            route_context = {
                "venue": args.venue,
                "source": args.source,
                "adapter": args.adapter,
                "route_kind": args.route_kind,
                "evidence_source": args.evidence_source,
            }
        elif args.adapter or args.route_kind != "index" or args.source != "official":
            raise HeadlessDiscoveryError("--venue is required for route-catalog metadata")
        # A request may carry its own normalized route context.  Construct the
        # catalog for that path too, otherwise ``HeadlessDiscovery`` falls back
        # to its default data directory and ignores the caller's --data-dir.
        request_has_route_context = isinstance(request, dict) and request.get("route_context") is not None
        route_catalog = RouteCatalog(args.data_dir) if route_context is not None or request_has_route_context else None
        result = (
            discovery.collect_raw(request, route_catalog=route_catalog, route_context=route_context)
            if args.raw
            else discovery.collect(request, route_catalog=route_catalog, route_context=route_context)
        )
    except (OSError, ValueError, json.JSONDecodeError, HeadlessDiscoveryError) as exc:
        result = {
            "schema_version": 1,
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
            "evidence": [],
            "facts_written": False,
            "materializer": "baseline_only",
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence = result.get("evidence", result.get("responses", []))
    print(json.dumps({"status": result["status"], "evidence": len(evidence), "facts_written": False}, ensure_ascii=False))
    return 0 if result["status"] in {"ok", "partial"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
