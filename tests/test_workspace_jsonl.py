import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.workspace_tools import workspace_jsonl_tool


def test_append_preserves_unicode_newlines_and_concurrent_records(tmp_path):
    context = ToolContext("session", "turn", tmp_path)
    tool = workspace_jsonl_tool()
    (tmp_path / "evidence.jsonl").write_text('{"first":true}', encoding="utf-8")
    def append(i):
        return tool.handler(context, {"path": "evidence.jsonl", "records": [{"case": i, "value": "中文\n🚀"}]})
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(result.ok for result in pool.map(append, range(12)))
    rows = [json.loads(line) for line in (tmp_path / "evidence.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0] == {"first": True}
    assert sorted(row["case"] for row in rows[1:]) == list(range(12))
    assert all(row["value"] == "中文\n🚀" for row in rows[1:])


def test_invalid_existing_file_and_nonfinite_input_leave_bytes_unchanged(tmp_path):
    context = ToolContext("session", "turn", tmp_path)
    tool = workspace_jsonl_tool()
    target = tmp_path / "evidence.jsonl"
    target.write_bytes(b'{"a":1}{"b":2}')
    result = tool.handler(context, {"path": "evidence.jsonl", "records": [{"new": True}]})
    assert not result.ok and result.data["line"] == 1
    assert target.read_bytes() == b'{"a":1}{"b":2}'
    with pytest.raises(ValueError):
        tool.handler(context, {"path": "evidence.jsonl", "records": [{"value": float("nan")}]})
    assert target.read_bytes() == b'{"a":1}{"b":2}'


def test_outside_workspace_is_rejected(tmp_path):
    context = ToolContext("session", "turn", tmp_path)
    with pytest.raises((ValueError, PermissionError)):
        workspace_jsonl_tool().handler(context, {"path": "../outside.jsonl", "records": [{"value": 1}]})


def test_failed_atomic_replace_preserves_original_and_removes_temp(tmp_path, monkeypatch):
    import app.agent_runtime.workspace_tools as module
    context = ToolContext("session", "turn", tmp_path)
    target = tmp_path / "evidence.jsonl"
    target.write_text('{"old":true}\n', encoding="utf-8")
    def fail_replace(*args):
        raise OSError("replace unavailable")
    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError):
        workspace_jsonl_tool().handler(context, {"path": "evidence.jsonl", "records": [{"new": True}]})
    assert target.read_text(encoding="utf-8") == '{"old":true}\n'
    assert list(tmp_path.iterdir()) == [target]
