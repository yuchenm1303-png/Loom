"""Measure how often each model talks between tool calls, from real run logs.

A "reply" is one model response that carried tool calls; it is "narrated" when
it also carried visible text.  Prompt wording can shorten that text but, for
some models, not make it rarer, so look at the rate before and after any
change to progress instructions or to how commentary is presented:

    python scripts/narration_cadence.py
    python scripts/narration_cadence.py --root D:/loom-home/agent_runtime/sessions --min-replies 50

Read-only.  It parses each session's events.jsonl and never calls a model.
Sessions written before model ids were logged fall back to session.json.
Different sessions ran different prompt versions and tasks, so treat the table
as a measurement of habit, not as a controlled comparison.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator


def tool_replies(events: Iterable[dict]) -> Iterator[tuple[str, int, int]]:
    """Yield (model, visible characters, tool call count) for each tool-bearing reply."""
    for event in events:
        if event.get("kind") != "model_response":
            continue
        data = event.get("data") or {}
        calls = data.get("tool_calls") or []
        if not calls:
            continue
        yield str(data.get("model") or ""), len(str(data.get("text") or "").strip()), len(calls)


def read_events(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                yield event


def session_model(directory: Path) -> str:
    try:
        return str(json.loads((directory / "session.json").read_text(encoding="utf-8")).get("model") or "")
    except (OSError, ValueError):
        return ""


def summarize(per_session: dict[str, list[tuple[str, int, int]]]) -> list[dict]:
    """Aggregate per-session replies into one row per model."""
    grouped: dict[str, dict] = defaultdict(lambda: {"sessions": set(), "replies": 0, "narrated": 0, "calls": 0, "chars": []})
    for session_id, replies in per_session.items():
        for model, chars, calls in replies:
            row = grouped[model or "?"]
            row["sessions"].add(session_id)
            row["replies"] += 1
            row["calls"] += calls
            if chars:
                row["narrated"] += 1
                row["chars"].append(chars)
    rows = []
    for model, row in grouped.items():
        narrated = row["narrated"]
        rows.append({
            "model": model,
            "sessions": len(row["sessions"]),
            "replies": row["replies"],
            "narrated_pct": round(100 * narrated / row["replies"]),
            "median_chars": int(statistics.median(row["chars"])) if row["chars"] else 0,
            "calls_per_narration": round(row["calls"] / narrated, 1) if narrated else None,
        })
    return sorted(rows, key=lambda item: item["replies"], reverse=True)


def collect(root: Path) -> dict[str, list[tuple[str, int, int]]]:
    per_session: dict[str, list[tuple[str, int, int]]] = {}
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        events = directory / "events.jsonl"
        if not events.is_file():
            continue
        fallback = session_model(directory)
        per_session[directory.name] = [(model or fallback, chars, calls) for model, chars, calls in tool_replies(read_events(events))]
    return per_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.home() / ".loom" / "agent_runtime" / "sessions")
    parser.add_argument("--min-replies", type=int, default=20, help="hide models with fewer tool-bearing replies")
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        print(f"No session directory at {args.root}", file=sys.stderr)
        return 2
    rows = [row for row in summarize(collect(args.root)) if row["replies"] >= args.min_replies]
    print(f"{'model':<32}{'sessions':>9}{'replies':>9}{'narrated':>10}{'median chars':>14}{'calls/narration':>17}")
    for row in rows:
        calls = "-" if row["calls_per_narration"] is None else f"{row['calls_per_narration']:.1f}"
        print(f"{row['model']:<32}{row['sessions']:>9}{row['replies']:>9}{str(row['narrated_pct']) + '%':>10}{row['median_chars']:>14}{calls:>17}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
