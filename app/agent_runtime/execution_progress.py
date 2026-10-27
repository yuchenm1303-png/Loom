"""Deterministic recovery accounting. Model prose never resets a failure budget."""
from __future__ import annotations


def unresolved_operation_loop(events) -> str | None:
    failures: dict[str, int] = {}
    for event in events:
        kind = getattr(event.kind, "value", event.kind)
        if kind == "turn_diff_updated":
            failures.clear()
        elif kind == "tool_completed" and str(event.data.get("tool", "")).startswith("browser_"):
            data = event.data.get("data") or {}
            if event.data.get("tool") == "browser_state" and data.get("effect") == "unchanged":
                key = "unchanged:" + str(data.get("browser_id") or "browser")
                failures[key] = failures.get(key, 0) + 1
                if failures[key] >= 3:
                    return key
            else:
                failures.clear()
        elif kind == "tool_failed":
            data = event.data.get("data") or {}
            operation = data.get("operation_id")
            threshold = 2
            if data.get("invalid_request_fingerprint"):
                operation = "schema:" + str(data["invalid_request_fingerprint"])
                threshold = 3
            elif data.get("failure_kind") != "browser_operation_unresolved" or not operation:
                continue
            failures[operation] = failures.get(operation, 0) + 1
            if failures[operation] >= threshold:
                return str(operation)
    return None
