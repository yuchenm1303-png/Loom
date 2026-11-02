"""Per-call digest of a live run's events (test-form runs only; safe to print)."""
import json
import sys
from pathlib import Path

for path in sorted(Path(sys.argv[1]).glob("agent_runtime/sessions/*/events.jsonl")):
    print("==", path.parent.name[:8])
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        kind, data = event["kind"], event.get("data") or {}
        stamp = event["created_at"][11:19]
        if kind == "model_response":
            calls = [(c["name"], json.dumps(c.get("arguments"), ensure_ascii=False)[:260]) for c in data.get("tool_calls") or []]
            print(stamp, "MODEL", data.get("model_execution_ms"), "ms", (data.get("text") or "")[:80].replace("\n", " "), calls)
        elif kind in {"tool_completed", "tool_failed"}:
            payload = data.get("data") or {}
            batch = payload.get("batch")
            extra = f" batch={batch.get('executed')}/{batch.get('requested')} stop={batch.get('stopped_reason')}" if batch else ""
            print(stamp, kind.upper(), data.get("tool"), "ok=" + str(data.get("ok")), extra,
                  str(data.get("content") or data.get("error") or "")[:300].replace("\n", " "))
