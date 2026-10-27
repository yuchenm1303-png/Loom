"""Offline timing replay. Reads logs; never calls a provider or executes tools."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent_runtime.contracts import AgentEvent, AgentEventKind
from app.agent_runtime.turn_timing import turn_timing_metadata


def audit(path: Path, turn_id: str) -> dict:
    events = []
    for line in path.open(encoding="utf-8"):
        payload = json.loads(line)
        if payload.get("turn_id") != turn_id:
            continue
        events.append(AgentEvent(**{**payload, "kind": AgentEventKind(payload["kind"])}))
    if not events:
        raise ValueError("turn not found")
    terminal = events[-1]
    metadata = turn_timing_metadata(events, turn_id=turn_id, kind=AgentEventKind.TURN_COMPLETED, now=terminal.created_at)
    inputs = [e.data.get("usage", {}).get("input_tokens", 0) for e in events if e.kind is AgentEventKind.MODEL_RESPONSE]
    return {"turn_id": turn_id, "timing": metadata,
            "input_tokens_min": min(inputs, default=0), "input_tokens_max": max(inputs, default=0),
            "failures_by_tool": dict(Counter(str(e.data.get("tool", "")) for e in events if e.kind is AgentEventKind.TOOL_FAILED))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--turn-id", required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.events, args.turn_id), ensure_ascii=False, indent=2))
