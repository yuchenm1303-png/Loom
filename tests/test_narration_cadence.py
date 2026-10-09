import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import narration_cadence as cadence


def response(text, calls, model="M"):
    data = {"text": text, "tool_calls": [{"name": "t"}] * calls}
    if model:
        data["model"] = model
    return {"kind": "model_response", "data": data}


def write_session(root, name, events, session_model=None):
    directory = root / name
    directory.mkdir()
    (directory / "events.jsonl").write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    if session_model:
        (directory / "session.json").write_text(json.dumps({"model": session_model}), encoding="utf-8")


def test_only_tool_bearing_responses_count_and_blank_text_is_not_narration():
    events = [
        response("", 1), response("   \n", 1), response("a" * 10, 2), response("b" * 30, 1),
        response("final answer", 0),
        {"kind": "tool_completed", "data": {"text": "ignored"}},
    ]
    assert list(cadence.tool_replies(events)) == [("M", 0, 1), ("M", 0, 1), ("M", 10, 2), ("M", 30, 1)]


def test_rows_report_rate_median_and_calls_per_narration(tmp_path):
    write_session(tmp_path, "one", [response("", 1), response("a" * 10, 1), response("b" * 30, 3), response("", 1)])
    write_session(tmp_path, "two", [response("c" * 50, 1)])
    [row] = cadence.summarize(cadence.collect(tmp_path))
    assert row == {"model": "M", "sessions": 2, "replies": 5, "narrated_pct": 60,
                   "median_chars": 30, "calls_per_narration": 2.3}


def test_models_missing_from_old_events_fall_back_to_the_session_file(tmp_path):
    write_session(tmp_path, "old", [response("hello", 1, model=None)], session_model="Legacy")
    write_session(tmp_path, "new", [response("", 1, model="Fresh")])
    rows = {row["model"]: row for row in cadence.summarize(cadence.collect(tmp_path))}
    assert rows["Legacy"]["narrated_pct"] == 100
    assert rows["Fresh"]["narrated_pct"] == 0 and rows["Fresh"]["calls_per_narration"] is None


def test_damaged_lines_are_skipped_not_fatal(tmp_path):
    directory = tmp_path / "broken"
    directory.mkdir()
    (directory / "events.jsonl").write_text('not json\n' + json.dumps(response("ok", 1)) + "\n[1, 2]\n", encoding="utf-8")
    assert list(cadence.tool_replies(cadence.read_events(directory / "events.jsonl"))) == [("M", 2, 1)]


def test_cli_prints_a_table_and_rejects_a_missing_root(tmp_path, capsys):
    write_session(tmp_path, "s", [response("x", 1)] * 3)
    assert cadence.main(["--root", str(tmp_path), "--min-replies", "1"]) == 0
    assert "M" in capsys.readouterr().out
    assert cadence.main(["--root", str(tmp_path / "missing")]) == 2
