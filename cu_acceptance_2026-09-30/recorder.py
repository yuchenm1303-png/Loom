"""Append structured Computer Use acceptance results to results.jsonl.

Usage from PowerShell:
  .\.venv\Scripts\python.exe cu_acceptance_2026-09-30\\recorder.py --record '{"case":"M6-notepad","action":"clear_text","ok":true,"native":false,"fallback_used":true,"semantic_verified":false,"effect":"changed","visual_delta_ratio":0.007812,"note":"single clear_text call, doc empty"}'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid


def record(path: str, payload: dict[str, object]) -> dict[str, object]:
    entry = dict(payload)
    entry.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    entry.setdefault("id", str(uuid.uuid4()))
    with open(path, "a", encoding="utf-8") as fp:
        fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default=os.path.join(
            os.path.dirname(__file__), "results.jsonl"
        ),
    )
    parser.add_argument("--record", required=True)
    args = parser.parse_args(argv)
    payload = json.loads(args.record)
    entry = record(args.out, payload)
    print(json.dumps(entry, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))