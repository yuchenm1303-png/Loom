"""Compare cold selective state reads with whole-log projection.

Optional --source-log copies a real log into a temporary directory; the source
is never opened for writing and no conversation text is printed.
"""
import argparse
import json
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.agent_runtime.contracts import AgentEventKind as E
from app.agent_runtime.storage import FileAgentSessionStore, _context_event_projection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-log", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="loom-startup-benchmark-") as temporary:
        root = Path(temporary)
        directory = root / "agent_runtime" / "sessions" / "abcdef"
        directory.mkdir(parents=True)
        log = directory / "events.jsonl"
        if args.source_log:
            shutil.copyfile(args.source_log, log)
        else:
            with log.open("w", encoding="utf-8", newline="\n") as handle:
                for i in range(3000):
                    payload = {"event_id": str(i), "session_id": "abcdef", "turn_id": f"turn-{i // 20}",
                        "kind": "verification_updated" if i % 20 == 0 else "tool_completed",
                        "created_at": "2026-10-27T00:00:00+00:00", "data": {
                            "subjects": {}, "content": "Historical observation " * 500}}
                    handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
        timings = {"whole_log_ms": [], "selected_cold_ms": [], "selected_warm_ms": []}
        for _ in range(max(1, args.repeats)):
            store = FileAgentSessionStore(root)
            start = time.perf_counter()
            old = store._view("abcdef", project=_context_event_projection,
                select=lambda records: [r for r in records if r["kind"] == "verification_updated"][-1:])
            timings["whole_log_ms"].append((time.perf_counter() - start) * 1000)
            store = FileAgentSessionStore(root)
            start = time.perf_counter()
            new = store.context_select("abcdef", kinds=(E.VERIFICATION_UPDATED,), last=1)
            timings["selected_cold_ms"].append((time.perf_counter() - start) * 1000)
            assert old == new, "selective read changed event semantics"
            start = time.perf_counter()
            assert store.context_select("abcdef", kinds=(E.VERIFICATION_UPDATED,), last=1) == new
            timings["selected_warm_ms"].append((time.perf_counter() - start) * 1000)
        print(json.dumps({"log_bytes": log.stat().st_size,
            **{key: round(statistics.median(values), 2) for key, values in timings.items()}}, indent=2))


if __name__ == "__main__":
    main()
