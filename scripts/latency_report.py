"""Read-only, content-free diagnostics from existing events.jsonl (stdlib only).

Run: python scripts/latency_report.py PATH [--turn-id ID]
Durations are diagnostic spans, not additive billing or a critical-path model.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path


def elapsed(start, end):
    try:
        value = (datetime.fromisoformat(end.replace("Z", "+00:00")) -
                 datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds() * 1000
        return round(value, 3) if value >= 0 else None
    except (TypeError, ValueError, AttributeError):
        return None


def number(value):
    return value if isinstance(value, (float, int)) and not isinstance(value, bool) and value >= 0 else None


def report(events, turn_id):
    selected = [e for e in events if e.get("turn_id") == turn_id]
    models, tools, retries, checks = [], [], [], []
    pending, calls, requested = {}, {}, {}
    seen_preparation = set()
    for event in selected:
        kind, data = event["kind"], event.get("data", {})
        step = data.get("step_id")
        stamp = event.get("created_at")
        if kind == "model_requested":
            # A transport retry can reuse a step ID. Retain every attempt and
            # close only the latest unmatched request for that step.
            prep = number(data.get("request_preparation_ms"))
            legacy_reuse = step in seen_preparation and "request_preparation_reused" not in data
            row = {"step_id": step, "request_event_id": event.get("event_id"),
                   "requested_at": stamp, "status": "unclosed",
                   "configuration": {k: data.get(k) for k in
                       ("profile_id", "provider", "model", "reasoning", "requested_max_output_tokens", "permission_mode")},
                   "tools_digest": (data.get("request_layout") or {}).get("tools_digest"),
                   "message_count": number(data.get("message_count")),
                   "preparation_ms": None if legacy_reuse else prep,
                   "preceding_retry_wait_ms": number(data.get("retry_wait_ms")),
                   "legacy_preparation_ambiguous": legacy_reuse,
                   "estimated_input_tokens": number(data.get("estimated_input_tokens_after")),
                   "calibrated_input_tokens": number(data.get("calibrated_input_tokens_after"))}
            models.append(row)
            pending[step] = row
            seen_preparation.add(step)
        elif kind in {"model_response", "model_response_rejected"}:
            row = pending.pop(step, None)
            if row is None:
                continue
            duration = number(data.get("model_execution_ms"))
            if duration is None:
                duration = elapsed(row["requested_at"], stamp)
            timing = data.get("stream_timing") or {}
            first = number(timing.get("first_content_ms"))
            # first_content includes reasoning/tool fragments; it is NOT public TTFT.
            # Do not infer generation from a role-only first chunk.
            stream_total = number(timing.get("execution_ms"))
            row.update({"status": kind, "response_event_id": event.get("event_id"),
                        "reason": data.get("reason"), "execution_ms": duration,
                        "executor_queue_wait_ms": number(timing.get("queue_wait_ms")),
                        "first_chunk_ms": number(timing.get("first_chunk_ms")),
                        "first_substantive_ms": first,
                        "generation_ms": round(stream_total - first, 3)
                            if first is not None and stream_total is not None and stream_total >= first else None,
                        "provider_usage": {k: number((data.get("usage") or {}).get(k)) for k in
                            ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens")},
                        "actual_cost": None})
        elif kind == "model_transport_retry":
            retries.append({k: data.get(k) for k in
                            ("step_id", "reason", "attempt", "phase", "wait_ms", "signal", "will_retry")})
        elif kind == "tool_requested":
            requested[data.get("call_id")] = stamp
        elif kind == "tool_started":
            row = {"call_id": data.get("call_id"), "step_id": step, "tool": data.get("tool"),
                   "started_at": stamp, "status": "unclosed",
                   "permission_and_queue_ms": elapsed(requested.get(data.get("call_id")), stamp)}
            tools.append(row)
            calls[data.get("call_id")] = row
        elif kind in {"tool_completed", "tool_failed"}:
            row = calls.pop(data.get("call_id"), None)
            if row is not None:
                row.update(status=kind, completed_at=stamp, ok=data.get("ok"),
                           execution_ms=elapsed(row["started_at"], stamp))
        elif kind == "check_recorded":
            checks.append({k: data.get(k) for k in ("case_id", "verdict", "check_kind", "assessment_source")})
    starts = [e for e in selected if e["kind"] == "turn_started"]
    ends = [e for e in selected if e["kind"] in
            {"turn_completed", "turn_failed", "turn_cancelled", "turn_interrupted", "limit_reached"}]
    return {"turn_id": turn_id, "terminal_status": ends[-1]["kind"] if ends else None,
            "wall_ms": elapsed(starts[0].get("created_at"), ends[-1].get("created_at")) if starts and ends else None,
            "models": models, "tools": tools, "retries": retries, "quality_checks": checks,
            "notes": ["Spans may overlap; tool durations are per-call, not summed wall time.",
                      "Legacy missing timings stay unknown; timestamp spans include event overhead.",
                      "Provider usage is separate from local estimates. Legacy all-zero usage may mean unavailable.",
                      "Cached input is a subset of input, not extra input or proof of free usage.",
                      "Actual invoice cost is unknown; tokens alone do not establish a billed amount.",
                      "A completed turn is not proof of task quality; inspect independent quality checks.",
                      "SDK retries can be inside execution spans; retry rows are annotations, not additive durations.",
                      "Generation is elapsed time after substantive output, including stalls and termination, not active compute.",
                      "Backoff is recorded on the next request; a final cancelled wait is unknown."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", type=Path)
    parser.add_argument("--turn-id")
    args = parser.parse_args()
    # Fail on malformed/truncated JSON rather than silently drop execution evidence.
    events = [json.loads(line) for line in args.events.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    turns = [args.turn_id] if args.turn_id else list(dict.fromkeys(e.get("turn_id") for e in events if e.get("turn_id")))
    print(json.dumps([report(events, turn) for turn in turns], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
