"""Deterministic recovery accounting. Model prose never resets a failure budget."""
from __future__ import annotations


def unresolved_operation_loop(events) -> str | None:
    failures: dict[str, int] = {}
    thresholds: dict[str, int] = {}
    for event in events:
        kind = getattr(event.kind, "value", event.kind)
        if kind == "turn_diff_updated":
            failures.clear()
        elif kind == "tool_completed" and str(event.data.get("tool", "")).startswith("browser_"):
            data = event.data.get("data") or {}
            if event.data.get("tool") == "browser_state" and data.get("effect") == "unchanged":
                key = "unchanged:" + str(data.get("browser_id") or "browser")
                failures[key] = failures.get(key, 0) + 1
                thresholds[key] = 3
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
            elif data.get("execution_status") == "not_executed":
                # A fence receipt reports the original operation's state. It is
                # not another execution failure, especially when a read-only
                # observation is trying to reconcile an outstanding command.
                continue
            failures[operation] = failures.get(operation, 0) + 1
            thresholds[operation] = threshold
    # Evaluate the final state: a later successful observation or workspace
    # change can settle a failure sequence earlier in this same event batch.
    for operation, count in failures.items():
        if count >= thresholds[operation]:
            return str(operation)
    return None
