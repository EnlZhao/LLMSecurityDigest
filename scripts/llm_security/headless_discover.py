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


def main() -> int:
    parser = argparse.ArgumentParser(description="collect allowlisted headless browser evidence")
    parser.add_argument("--input", type=Path, required=True, help="JSON request containing only allowlisted URLs")
    parser.add_argument("--out", type=Path, required=True, help="evidence/raw-response JSON output path")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="capture bounded raw HTML/JSON/PDF response bytes and provenance",
    )
    args = parser.parse_args()
    if args.out.name.casefold() == "facts.json":
        print("headless output path cannot be facts.json", file=sys.stderr)
        return 2
    try:
        request = json.loads(args.input.read_text(encoding="utf-8"))
        discovery = HeadlessDiscovery()
        result = discovery.collect_raw(request) if args.raw else discovery.collect(request)
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
    print(json.dumps({"status": result["status"], "evidence": len(result["evidence"]), "facts_written": False}, ensure_ascii=False))
    return 0 if result["status"] in {"ok", "partial"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
