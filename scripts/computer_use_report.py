"""How real Computer Use turns spent their time (stdlib only, read-only).

Run: python scripts/computer_use_report.py [ROOT] [--since 2026-11-01] [--until ...] [--json]

ROOT defaults to ~/.loom/agent_runtime/sessions. A turn counts when it made at least
one computer_action call. Compare a window before a Computer Use change with one
after it: desktop actions per model round trip (what batching should raise), model
time against tool time (where a turn's wall time goes), failure share, and how
batches ended. Output is content-free: counts, durations and categories only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime
import json
from pathlib import Path
from statistics import median

_OBSERVE_ONLY = {"screenshot", "done", "call_user"}


def _stamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _actions(arguments):
    """The desktop actions one computer_action call asked for, in order."""

    batch = arguments.get("actions")
    if isinstance(batch, list):
        return [item for item in batch if isinstance(item, dict)]
    single = arguments.get("action")
    return [single] if isinstance(single, dict) else []


def turns(root: Path, since: date | None = None, until: date | None = None):
    for path in sorted(root.glob("*/events.jsonl")):
        current: dict[str, dict] = defaultdict(lambda: {
            "model_steps": 0, "computer_steps": 0, "calls": 0, "actions": 0, "failed_calls": 0,
            "model_ms": 0.0, "tool_ms": 0.0, "batch_ends": Counter(), "start": None, "end": None,
        })
        started_at: dict[str, datetime] = {}
        computer_step_ids: dict[str, set] = defaultdict(set)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            turn_id = event.get("turn_id")
            if not turn_id:
                continue
            kind, data = event.get("kind"), event.get("data") or {}
            stamp = _stamp(event.get("created_at", ""))
            turn = current[turn_id]
            if stamp is not None:
                turn["start"] = turn["start"] or stamp
                turn["end"] = stamp
            if kind == "model_response":
                turn["model_steps"] += 1
                turn["model_ms"] += float(data.get("model_execution_ms") or 0)
                if data.get("step_id") in computer_step_ids[turn_id]:
                    turn["computer_steps"] += 1
            if data.get("tool") != "computer_action":
                continue
            call_id = data.get("call_id")
            if kind == "tool_requested":
                turn["calls"] += 1
                computer_step_ids[turn_id].add(data.get("step_id"))
                turn["actions"] += sum(
                    1 for item in _actions(data.get("arguments") or {})
                    if str(item.get("type") or "") not in _OBSERVE_ONLY
                )
            elif kind == "tool_started" and stamp is not None:
                started_at[call_id] = stamp
            elif kind in {"tool_completed", "tool_failed"}:
                if kind == "tool_failed" or data.get("ok") is False:
                    turn["failed_calls"] += 1
                if call_id in started_at and stamp is not None:
                    turn["tool_ms"] += (stamp - started_at.pop(call_id)).total_seconds() * 1000
                batch = (data.get("data") or {}).get("batch")
                if isinstance(batch, dict):
                    turn["batch_ends"][str(batch.get("stopped_reason") or "completed")] += 1
        for turn_id, turn in current.items():
            if not turn["calls"] or turn["start"] is None:
                continue
            day = turn["start"].date()
            if (since and day < since) or (until and day > until):
                continue
            yield {"session": path.parent.name, "turn": turn_id, "day": day.isoformat(), **turn}


def summarize(rows):
    rows = list(rows)
    if not rows:
        return {"turns": 0}

    def mid(key):
        return round(median(row[key] for row in rows), 1)

    calls = sum(row["calls"] for row in rows)
    actions = sum(row["actions"] for row in rows)
    model_ms = sum(row["model_ms"] for row in rows)
    tool_ms = sum(row["tool_ms"] for row in rows)
    ends = Counter()
    for row in rows:
        ends.update(row["batch_ends"])
    return {
        "turns": len(rows),
        "computer_calls": calls,
        "desktop_actions": actions,
        "actions_per_call": round(actions / calls, 2) if calls else 0.0,
        "failed_call_share": round(sum(row["failed_calls"] for row in rows) / calls, 3) if calls else 0.0,
        "median_model_steps_per_turn": mid("model_steps"),
        "median_computer_calls_per_turn": mid("calls"),
        "median_wall_s_per_turn": round(median((row["end"] - row["start"]).total_seconds() for row in rows), 1),
        "model_share_of_model_plus_tool": round(model_ms / (model_ms + tool_ms), 3) if model_ms + tool_ms else 0.0,
        "mean_model_ms_per_step": round(model_ms / max(1, sum(row["model_steps"] for row in rows))),
        "mean_tool_ms_per_call": round(tool_ms / calls) if calls else 0,
        "batch_ends": dict(ends),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", default=str(Path.home() / ".loom" / "agent_runtime" / "sessions"))
    parser.add_argument("--since", type=date.fromisoformat)
    parser.add_argument("--until", type=date.fromisoformat)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary = summarize(turns(Path(args.root), args.since, args.until))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    for key, value in summary.items():
        print(f"{key:34} {value}")


if __name__ == "__main__":
    main()
