import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import turn_efficiency_report as report


def event(kind, turn="t1", at="2026-10-29T04:00:00+00:00", **data):
    return {"kind": kind, "turn_id": turn, "session_id": "s", "created_at": at, "data": data}


def write_session(root, name, events):
    directory = root / name
    directory.mkdir()
    (directory / "events.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
                                            encoding="utf-8")


def test_failure_causes_cover_the_field_cases():
    cause = report.failure_cause
    assert cause("exec", "exit=1\nstderr:\n[eval]:1\n\"const\nUnterminated string constant") == "shell_quoting"
    assert cause("exec", "exit=255\nstderr:\n'tail' 不是内部或外部命令，也不是可运行的程序") == "command_missing"
    assert cause("exec", "exit=1\nstderr:\n'python' �����ڲ����ⲿ���Ҳ���") == "command_missing"
    assert cause("replace_workspace_text", "old_text was not found; no file was changed") == "stale_edit"
    assert cause("apply_patch", "ValueError: update target does not exist: scratch:a.mjs") == "missing_path"
    assert cause("record_check", "A visual pass requires a current-generation executed interaction") \
        == "verification_rejected"
    assert cause("exec", "Invalid tool request (exec): $: arguments violate schema rule") == "invalid_call"
    assert cause("exec", "process=p status=exited backend=pipe\nexit=1\nstdout:\n1 failed") == "nonzero_exit"
    assert cause("exec", "something new") == "other"


def test_turns_are_summarized_per_model_without_content(tmp_path):
    write_session(tmp_path, "a", [
        event("turn_started", at="2026-10-29T04:00:00+00:00"),
        event("model_requested", model="m1"),
        event("model_response", usage={"input_tokens": 100, "cached_input_tokens": 90}),
        event("tool_started", call_id="c1"),
        event("tool_failed", call_id="c1", tool="exec", content="old_text was not found SECRET"),
        event("tool_started", call_id="c2"),
        event("verification_finish_requested", attempt=1),
        event("model_requested", model="m1"),
        event("model_response", usage={"input_tokens": 100, "cached_input_tokens": 100}),
        event("turn_completed", at="2026-10-29T04:05:00+00:00", turn_elapsed_ms=300000),
        event("turn_started", turn="t2", at="2026-10-30T04:00:00+00:00"),  # still running: not counted
        event("model_requested", turn="t2", model="m1"),
    ])
    write_session(tmp_path, "b", [
        event("turn_started", turn="old", at="2026-10-01T00:00:00+00:00"),
        event("turn_completed", turn="old", at="2026-10-01T00:00:01+00:00"),
    ])
    rows = report.collect(tmp_path, since=date(2026, 10, 20))
    assert len(rows) == 1
    (summary,) = report.summarize(rows, "model")
    assert summary["model"] == "m1" and summary["turns"] == 1 and summary["completed"] == 1
    assert summary["round_trips_median"] == 2 and summary["elapsed_s_median"] == 300.0
    assert summary["failed_calls"] == 1 and summary["tool_calls"] == 2 and summary["failures"] == {"stale_edit": 1}
    assert summary["verification_recoveries_per_turn"] == 1.0 and summary["cache_hit"] == 0.95
    assert "SECRET" not in report.render([summary], "model")
