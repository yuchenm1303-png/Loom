from pathlib import Path

import pytest

from app.agent_runtime.diff_tracker import TurnDiffTracker
from app.agent_runtime.journal import repair_tail
from app.agent_runtime.patch_format import parse_text_patch
from app.agent_runtime.patch_runtime import ApplyPatchRuntime
from app.agent_runtime.tools import ToolContext


def context(path):
    return ToolContext("audit", "turn", path)


@pytest.mark.parametrize("original", [b"first\r\nsecond\r\n", b"first\nsecond\r\n"])
def test_structured_patch_preserves_unmodified_line_endings(tmp_path, original):
    target = tmp_path / "file.txt"
    target.write_bytes(original)
    ApplyPatchRuntime().apply(context(tmp_path), [{"action": "update", "path": "file.txt",
        "old_text": "second", "new_text": "changed"}], diff_tracker=TurnDiffTracker())
    assert target.read_bytes() == original.replace(b"second", b"changed")


@pytest.mark.parametrize("original", [b"first\r\nsecond\r\n", b"first\r\nsecond", b"first\nsecond\r\n"])
def test_text_patch_preserves_context_endings_and_final_newline(tmp_path, original):
    (tmp_path / "file.txt").write_bytes(original)
    patch = "*** Begin Patch\n*** Update File: file.txt\n@@\n first\n-second\n+changed\n*** End Patch"
    ApplyPatchRuntime().apply(context(tmp_path), parse_text_patch(context(tmp_path), patch), diff_tracker=TurnDiffTracker())
    result = (tmp_path / "file.txt").read_bytes()
    assert result == original.replace(b"second", b"changed")


def test_patch_failure_restores_original_bytes(tmp_path, monkeypatch):
    import app.agent_runtime.patch_runtime as module
    original = b"first\nsecond\r\n"
    (tmp_path / "a.txt").write_bytes(original)
    (tmp_path / "b.txt").write_bytes(b"original")
    replace = module.os.replace

    def fail_second(source, destination):
        if Path(destination).name == "b.txt":
            raise OSError("injected failure")
        return replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_second)
    with pytest.raises(OSError):
        ApplyPatchRuntime().apply(context(tmp_path), [
            {"action": "update", "path": "a.txt", "old_text": "second", "new_text": "changed"},
            {"action": "update", "path": "b.txt", "content": "changed"},
        ], diff_tracker=TurnDiffTracker())
    assert (tmp_path / "a.txt").read_bytes() == original


def test_utf8_write_limit_matches_read_limit(tmp_path):
    runtime = ApplyPatchRuntime()
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        runtime.apply(context(tmp_path), [{"action": "add", "path": "large.txt", "content": "中" * 400000}], diff_tracker=TurnDiffTracker())
    assert not (tmp_path / "large.txt").exists()
    runtime.apply(context(tmp_path), [{"action": "add", "path": "ok.txt", "content": "中" * 300000}], diff_tracker=TurnDiffTracker())
    runtime.apply(context(tmp_path), [{"action": "update", "path": "ok.txt", "content": "可编辑"}], diff_tracker=TurnDiffTracker())


@pytest.mark.parametrize("tail,expected", [(b'{"ok":true}', b'{"ok":true}\n'), (b'{"bad":', b''), (b'', b'')])
def test_journal_repair_only_changes_incomplete_tail(tmp_path, tail, expected):
    path = tmp_path / "events.jsonl"
    prefix = b'{"event":"old"}\n' * 2000
    path.write_bytes(prefix + tail)
    repair_tail(path)
    assert path.read_bytes() == prefix + expected


def test_normal_journal_tail_reads_one_byte(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"event":"old"}\n' * 10000)
    original_open = Path.open
    reads = []

    class RecordingFile:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def __getattr__(self, name): return getattr(self.handle, name)
        def read(self, size=-1):
            reads.append(size)
            return self.handle.read(size)

    def tracked_open(target, *args, **kwargs):
        handle = original_open(target, *args, **kwargs)
        return RecordingFile(handle) if target == path else handle

    monkeypatch.setattr(Path, "open", tracked_open)
    repair_tail(path)
    assert reads == [1]


def test_recent_events_read_only_tail_and_match_full_history(tmp_path, monkeypatch):
    import json
    from app.agent_runtime.storage import FileAgentSessionStore
    store = FileAgentSessionStore(tmp_path)
    directory = store.session_dir("abcdef")
    directory.mkdir()
    records = [{"event_id": str(index), "session_id": "abcdef", "turn_id": "turn",
                "kind": "model_response", "created_at": "now", "data": {"text": "x" * 1000}}
               for index in range(200)]
    path = directory / "events.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    expected = store.events("abcdef")[-3:]
    original_open = Path.open
    reads = []

    class RecordingFile:
        def __init__(self, handle): self.handle = handle
        def __enter__(self): return self
        def __exit__(self, *args): self.handle.close()
        def __getattr__(self, name): return getattr(self.handle, name)
        def read(self, size=-1):
            reads.append(size)
            return self.handle.read(size)

    def tracked_open(target, *args, **kwargs):
        handle = original_open(target, *args, **kwargs)
        return RecordingFile(handle) if target == path else handle

    monkeypatch.setattr(Path, "open", tracked_open)
    assert store.recent_events("abcdef", 3) == expected
    assert sum(reads) <= 8192
    assert store.recent_events("abcdef", 0) == ()
    with pytest.raises(ValueError):
        store.recent_events("abcdef", -1)


def test_tail_reader_handles_blank_rows_utf8_blocks_and_incomplete_tail(tmp_path):
    from app.agent_runtime.storage import _recent_event_lines
    path = tmp_path / "events.jsonl"
    large = ('{"text":"' + '中' * 4000 + '"}').encode()
    path.write_bytes(b'{"first":true}\n' + large + b'\n\n' * 5000 + b'{"incomplete":')
    with path.open("rb") as handle:
        assert _recent_event_lines(handle, 2) == [large + b"\n", b'{"incomplete":']


@pytest.mark.parametrize("mode,expected,full_reads", [("recent", 123, 0), ("old", 123, 1), ("checkpoint", None, 0)])
def test_usage_reads_tail_with_full_fallback_only_when_needed(mode, expected, full_reads):
    from types import SimpleNamespace
    from app.agent_runtime.context_budget import _latest_provider_context_tokens
    usage = SimpleNamespace(kind="model_response", data={"usage": {"total_tokens": 123}})
    other = SimpleNamespace(kind="tool_started", data={})

    class Store:
        reads = 0
        def events(self, _):
            self.reads += 1
            return [usage] + [other] * 240
        def recent_events(self, _, limit):
            if mode == "recent": return [usage]
            if mode == "checkpoint": return [SimpleNamespace(kind="context_checkpointed", data={})]
            return [other] * limit

    store = Store()
    result = _latest_provider_context_tokens(SimpleNamespace(store=store), SimpleNamespace(session_id="audit"))
    assert result == expected and store.reads == full_reads
