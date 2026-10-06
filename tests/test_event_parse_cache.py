import json
import os
import pytest
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.contracts import AgentEvent, AgentEventKind as E


def event(index):
    return AgentEvent(str(index), "abcdef", "turn", E.TOOL_COMPLETED, "2026-10-05T00:00:00Z", {"nested": {"index": index}})


def test_repeated_reads_parse_only_external_append(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    for index in range(3):
        store.append_event(event(index))
    assert len(store.events("abcdef")) == 3
    before = store._event_cache.metrics()["parsed_records"]
    for _ in range(5):
        assert len(store.events("abcdef")) == 3
    assert store._event_cache.metrics()["parsed_records"] == before
    other = FileAgentSessionStore(tmp_path)
    other.append_event(event(3))
    assert len(store.events("abcdef")) == 4
    assert store._event_cache.metrics()["parsed_records"] == before + 1


def test_returned_data_cannot_poison_cache(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    store.append_event(event(1))
    store.events("abcdef")[0].data["nested"]["index"] = "poison"
    assert store.events("abcdef")[0].data["nested"]["index"] == 1


def raw_event(index):
    item = event(index)
    return json.dumps({"event_id": item.event_id, "session_id": item.session_id,
        "turn_id": item.turn_id, "kind": item.kind.value, "created_at": item.created_at,
        "data": item.data}).encode()


def test_valid_and_partial_unterminated_tail_is_not_duplicated(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    path = store.session_dir("abcdef") / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw_event(1))
    assert [e.event_id for e in store.events("abcdef")] == ["1"]
    with path.open("ab") as handle:
        handle.write(b"\n" + raw_event(2)[:20])
    assert [e.event_id for e in store.events("abcdef")] == ["1"]
    with path.open("ab") as handle:
        handle.write(raw_event(2)[20:] + b"\n")
    assert [e.event_id for e in store.events("abcdef")] == ["1", "2"]


def test_truncation_rewrite_and_atomic_replace_invalidate_cache(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    for i in range(3):
        store.append_event(event(i))
    path = store.session_dir("abcdef") / "events.jsonl"
    store.events("abcdef")
    path.write_bytes(raw_event(8) + b"\n")
    assert [e.event_id for e in store.events("abcdef")] == ["8"]
    replacement = path.with_suffix(".new")
    replacement.write_bytes(raw_event(9) + b"\n")
    os.replace(replacement, path)
    assert [e.event_id for e in store.events("abcdef")] == ["9"]
    path.unlink()
    assert store.events("abcdef") == ()


def test_cold_bounded_tail_does_not_parse_old_rows(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    path = store.session_dir("abcdef") / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\n".join(raw_event(i) for i in range(1000)) + b"\n\n")
    assert [e.event_id for e in store.recent_events("abcdef", 3)] == ["997", "998", "999"]
    assert store._event_cache.metrics()["parsed_records"] == 3
    before = store._event_cache.metrics().copy()
    store.recent_events("abcdef", 3)
    assert store._event_cache.metrics()["parsed_records"] == before["parsed_records"]
    with path.open("ab") as handle:
        handle.write(raw_event(1000) + b"\n")
    assert [e.event_id for e in store.recent_events("abcdef", 3)] == ["998", "999", "1000"]
    # Changed bounded views reload only the requested tail, never full history.
    assert store._event_cache.metrics()["parsed_records"] == 6


def test_malformed_complete_record_remains_an_error(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    path = store.session_dir("abcdef") / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{broken}\n")
    with pytest.raises(json.JSONDecodeError):
        store.events("abcdef")


def test_cache_memory_and_entries_are_bounded(tmp_path):
    from app.agent_runtime.event_cache import EventParseCache
    store = FileAgentSessionStore(tmp_path)
    store._event_cache = EventParseCache(max_bytes=2000, max_entries=2)
    for session in ("a", "b", "c", "d"):
        path = store.session_dir(session) / "events.jsonl"
        path.parent.mkdir(parents=True)
        path.write_bytes(raw_event(1) + b"\n")
        store.events(session)
    metrics = store._event_cache.metrics()
    assert metrics["entries"] <= 2
    assert metrics["cached_bytes"] <= 2000


def test_recovered_commit_after_cached_read_is_visible_once(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    store.append_event(event(1))
    assert len(store.events("abcdef")) == 1
    directory = store.session_dir("abcdef")
    (directory / ".pending-commit.json").write_text(json.dumps({
        "session": {}, "event": json.loads(raw_event(2))}), encoding="utf-8")
    assert [e.event_id for e in store.events("abcdef")] == ["1", "2"]
    assert store._event_cache.metrics()["parsed_records"] == 2
    assert [e.event_id for e in store.events("abcdef")] == ["1", "2"]


def test_same_size_rewrite_and_truncate_regrow_invalidate(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    path = store.session_dir("abcdef") / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw_event(1) + b"\n")
    store.events("abcdef")
    old = path.stat()
    path.write_bytes(raw_event(2) + b"\n")
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
    assert [e.event_id for e in store.events("abcdef")] == ["2"]
    path.write_bytes(raw_event(3) + b"\n" + raw_event(4) + b"\n")
    assert [e.event_id for e in store.events("abcdef")] == ["3", "4"]
