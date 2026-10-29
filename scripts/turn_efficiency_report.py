"""How efficiently real agent turns ran, across every session (stdlib only, read-only).

Run: python scripts/turn_efficiency_report.py [ROOT] [--since 2026-10-29] [--until ...] [--by model|day|week] [--json]

ROOT defaults to ~/.loom/agent_runtime/sessions. Compare a window before a prompt or
runtime change with one after it: model round trips, failed tool calls (by cause),
verification recoveries and wall time per turn. Output is content-free: no user text,
no tool output, only counts, durations and failure categories.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from statistics import median

_ENDS = {"turn_completed": "completed", "turn_failed": "failed", "turn_cancelled": "cancelled",
         "turn_interrupted": "interrupted"}

# First match wins. Matched against the start of a failed tool call's content.
# Windows console output often arrives as GBK read as UTF-8: "ڲ����ⲿ" is what
# "内部或外部" (cmd's "not an internal or external command") turns into.
_FAILURES = (
    ("invalid_call", ("invalid tool request", "not executed: ", "is not in this step's tool list",
                      "violate schema", "evidence references are invalid")),
    ("verification_rejected", ("a visual pass requires",)),
    ("stale_edit", ("old_text was not found", "old_text matched", "preimage changed", "no file was changed")),
    ("command_missing", ("is not recognized as", "commandnotfoundexception", "command not found",
                         "executable not found", "exit=9009", "不是内部或外部命令", "无法将", "ڲ����ⲿ")),
    ("shell_quoting", ("unterminated string", "unexpected eof", "syntaxerror", "parsererror",
                       "missing the terminator", "unexpected token", "findstr:")),
    ("missing_path", ("does not exist", "no such file", "cannot find the path", "cannot find the file", "enoent",
                      "cannot find module", "could not read", "must be a directory", "eisdir")),
    ("browser", ("browsererror", "browser resource is unavailable", "native browser", "navigation failed",
                 "browser automation", "current-browser backend", "tool runtime service is unavailable")),
    ("network", ("connection closed by", "connection refused", "err_connection", "could not resolve host")),
    ("timeout", ("timed out", "timed_out", "timeout")),
    ("denied", ("access is denied", "permission denied", "not approved", "approval")),
    # A command that ran and exited non-zero: failing tests, a rejected push, grep with no match.
    ("nonzero_exit", ("status=exited",)),
)


def failure_cause(tool, content):
    text = str(content or "")[:600].casefold()
    for cause, marks in _FAILURES:
        if any(mark in text for mark in marks):
            return cause
    return "other"


def _stamp(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def turns_in(path):
    """One record per finished turn in one events.jsonl."""
    open_turns, done = {}, []
    with open(path, encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            turn_id, kind, data = event.get("turn_id"), event.get("kind"), event.get("data") or {}
            if not turn_id:
                continue
            if kind == "turn_started":
                open_turns[turn_id] = {"turn_id": turn_id, "session_id": event.get("session_id"),
                                       "started": event.get("created_at"), "models": Counter(), "round_trips": 0,
                                       "tool_calls": 0, "failures": Counter(), "recoveries": 0,
                                       "input_tokens": 0, "cached_tokens": 0}
                continue
            turn = open_turns.get(turn_id)
            if turn is None:
                continue
            if kind == "model_requested":
                turn["round_trips"] += 1
                turn["models"][str(data.get("model") or "unknown")] += 1
            elif kind == "model_response":
                usage = data.get("usage") or {}
                turn["input_tokens"] += int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
                turn["cached_tokens"] += int(usage.get("cached_input_tokens") or 0)
            elif kind == "tool_started":
                turn["tool_calls"] += 1
            elif kind == "tool_failed":
                turn["failures"][failure_cause(data.get("tool"), data.get("content"))] += 1
            elif kind == "verification_finish_requested":
                turn["recoveries"] += 1
            elif kind in _ENDS:
                start, end = _stamp(turn["started"]), _stamp(event.get("created_at"))
                elapsed = data.get("turn_elapsed_ms")
                if not isinstance(elapsed, (int, float)) and start and end:
                    elapsed = (end - start).total_seconds() * 1000
                turn.update(outcome=_ENDS[kind], elapsed_s=round((elapsed or 0) / 1000, 1),
                            model=turn["models"].most_common(1)[0][0] if turn["models"] else "unknown")
                del turn["models"]
                done.append(open_turns.pop(turn_id))
    return done


def collect(root, since=None, until=None):
    rows = []
    for path in sorted(Path(root).glob("*/events.jsonl")):
        for turn in turns_in(path):
            started = _stamp(turn["started"])
            day = started.date() if started else None
            if day is None or (since and day < since) or (until and day > until):
                continue
            turn["day"] = day.isoformat()
            turn["week"] = (day - timedelta(days=day.weekday())).isoformat()
            rows.append(turn)
    return rows


def _quantile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0


def summarize(rows, by):
    groups = defaultdict(list)
    for row in rows:
        groups[row[by]].append(row)
    out = []
    for key, items in sorted(groups.items()):
        working = [r for r in items if r["tool_calls"]]  # turns that used tools at all
        failures = Counter()
        for r in items:
            failures.update(r["failures"])
        calls = sum(r["tool_calls"] for r in items)
        tokens = sum(r["input_tokens"] for r in items)
        out.append({
            by: key, "turns": len(items), "tool_turns": len(working),
            "completed": sum(r["outcome"] == "completed" for r in items),
            "elapsed_s_median": round(median([r["elapsed_s"] for r in working]), 1) if working else 0,
            "elapsed_s_p75": _quantile([r["elapsed_s"] for r in working], 0.75),
            "round_trips_median": median([r["round_trips"] for r in working]) if working else 0,
            "round_trips_p75": _quantile([r["round_trips"] for r in working], 0.75),
            "tool_calls": calls, "failed_calls": sum(failures.values()),
            "failed_call_rate": round(sum(failures.values()) / calls, 3) if calls else 0,
            "failures": dict(failures.most_common()),
            "verification_recoveries_per_turn": round(sum(r["recoveries"] for r in working) / len(working), 2)
            if working else 0,
            "cache_hit": round(sum(r["cached_tokens"] for r in items) / tokens, 3) if tokens else None,
        })
    return out


def render(summary, by):
    lines = []
    for row in summary:
        lines.append(
            f"{row[by]}: {row['tool_turns']}/{row['turns']} working turns, {row['completed']} completed | "
            f"time median {row['elapsed_s_median']}s p75 {row['elapsed_s_p75']}s | "
            f"round trips median {row['round_trips_median']} p75 {row['round_trips_p75']} | "
            f"failed calls {row['failed_calls']}/{row['tool_calls']} ({row['failed_call_rate']:.1%}) "
            f"{row['failures']} | verification recoveries/turn {row['verification_recoveries_per_turn']} | "
            f"cache hit {row['cache_hit']}")
    return "\n".join(lines) or "No finished turns in range."


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", nargs="?", type=Path, default=Path.home() / ".loom" / "agent_runtime" / "sessions")
    parser.add_argument("--since", type=date.fromisoformat, help="first UTC day to include")
    parser.add_argument("--until", type=date.fromisoformat, help="last UTC day to include")
    parser.add_argument("--by", choices=("model", "day", "week"), default="model")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    summary = summarize(collect(args.root, args.since, args.until), args.by)
    print(json.dumps(summary, ensure_ascii=False, indent=2) if args.json else render(summary, args.by))


if __name__ == "__main__":
    main()
