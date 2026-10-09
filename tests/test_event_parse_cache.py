import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from app.agent_runtime import event_cache, storage
from app.agent_runtime.event_cache import EventParseCache, _clone
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.contracts import AgentEvent, AgentEventKind as E


def event(index):
    return AgentEvent(str(index), "abcdef", "turn", E.TOOL_COMPLETED, "2026-10-05T00:00:00Z", {"nested": {"index": index}})


def test_context_projection_caches_large_history_without_losing_durable_results(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    store._event_cache = EventParseCache(max_bytes=8_000_000)
    payload = "large observation " * 600000  # exceeds this full-payload cache budget
    store.append_event(AgentEvent("large", "abcdef", "turn", E.TOOL_COMPLETED, "2026-10-05T00:00:00Z",
                                  {"call_id": "verify", "content": payload}))
    plan = {"plan": [{"step": "Verify", "status": "completed", "outcome": "passed",
                      "evidence_refs": [{"call_id": "verify"}]}]}
    store.append_event(AgentEvent("plan", "abcdef", "turn", E.PLAN_UPDATED, "2026-10-05T00:00:01Z", plan))
    projected = store.context_events("abcdef")
    assert projected[0].data == {}
    assert projected[1].data == plan
    metrics = store._event_cache.metrics()
    assert store.context_events("abcdef") == projected
    assert store._event_cache.metrics()["parsed_records"] == metrics["parsed_records"]
    # A newly appended event extends the projection; older bodies aren't reparsed.
    store.append_event(event(3))
    assert len(store.context_events("abcdef")) == 3
    assert store._event_cache.metrics()["parsed_records"] == metrics["parsed_records"] + 1
    projected[1].data["plan"][0]["step"] = "caller mutation"
    assert store.context_events("abcdef")[1].data == plan
    assert store.events("abcdef")[0].data["content"] == payload


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


def test_slow_session_parse_does_not_block_another_session(tmp_path, monkeypatch):
    from app.agent_runtime import storage

    store = FileAgentSessionStore(tmp_path)
    for session in ("aaa", "bbb"):
        path = store.session_dir(session) / "events.jsonl"
        path.parent.mkdir(parents=True)
        path.write_bytes(raw_event(1) + b"\n")
    started, release = threading.Event(), threading.Event()
    tail_reader = storage._recent_event_lines

    def delayed_tail(handle, limit):
        if Path(handle.handle.name).parent.name == "aaa":
            started.set()
            assert release.wait(3)
        return tail_reader(handle, limit)

    monkeypatch.setattr(storage, "_recent_event_lines", delayed_tail)
    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(store.recent_events, "aaa", 1)
        try:
            assert started.wait(2)
            fast = pool.submit(store.recent_events, "bbb", 1)
            assert [e.event_id for e in fast.result(timeout=2)] == ["1"]
            assert not slow.done()
        finally:
            release.set()
        assert [e.event_id for e in slow.result(timeout=2)] == ["1"]
    assert store._event_cache.metrics()["parsed_records"] == 2


def test_clone_copies_parsed_json_without_sharing_containers():
    original = {"a": [1, {"b": "text", "c": None}, 2.5, True], "d": {"e": []}}
    copy = _clone(original)
    assert copy == original
    assert copy is not original and copy["a"] is not original["a"]
    assert copy["a"][1] is not original["a"][1] and copy["d"]["e"] is not original["d"]["e"]
    copy["a"][1]["b"] = "changed"
    assert original["a"][1]["b"] == "text"
    # Anything JSON cannot produce still copies the way it always did.
    assert _clone({"pair": (1, [2])}) == {"pair": (1, [2])}
    assert _clone({"pair": (1, [2])})["pair"][1] is not None


def test_weight_counts_each_object_once_and_can_stop_early():
    shared = "x" * 100
    value = {"first": [shared, shared, {"nested": [shared, 1.5, None]}], "second": {"k": shared}}

    def reference(item, seen=None):
        seen = set() if seen is None else seen
        if id(item) in seen:
            return 0
        seen.add(id(item))
        size = sys.getsizeof(item)
        if isinstance(item, dict):
            size += sum(reference(k, seen) + reference(v, seen) for k, v in item.items())
        elif isinstance(item, (list, tuple)):
            size += sum(reference(child, seen) for child in item)
        return size

    assert EventParseCache._weight(value) == reference(value)
    big = [{"index": index, "text": "y" * 50} for index in range(2000)]
    exact = EventParseCache._weight(big)
    stopped = EventParseCache._weight(big, limit=1000)
    assert 1000 < stopped < exact // 10


def _count_weighing_and_copies(monkeypatch):
    """Record every record weighed and every event body copied from the cache."""
    weighed, copies = [], []
    original = EventParseCache._weight

    def counting(value, seen=None, limit=None):
        weighed.append(value)
        return original(value, seen, limit)

    def counting_clone(value):
        if isinstance(value, dict) and "nested" in value:  # an event body; _clone recurses through this name
            copies.append(value)
        return _clone(value)

    monkeypatch.setattr(EventParseCache, "_weight", staticmethod(counting))
    monkeypatch.setattr(event_cache, "_clone", counting_clone)
    monkeypatch.setattr(storage, "_clone", counting_clone)  # the store copies the bodies it builds events from
    return weighed, copies


def test_history_too_large_to_keep_is_not_copied_or_weighed_again(tmp_path, monkeypatch):
    store = FileAgentSessionStore(tmp_path)
    store._event_cache = EventParseCache(max_bytes=6000)
    for index in range(20):
        store.append_event(event(index))
    weighed, copies = _count_weighing_and_copies(monkeypatch)

    first = store.events("abcdef")
    assert len(first) == 20 and store._event_cache.metrics()["entries"] == 0
    # Weighing stops once the history is known not to fit, and nothing is copied.
    assert 0 < len(weighed) < 10 and copies == []
    # The caller owns what the cache refused to keep, and a later read is not
    # affected by what was done to it.
    first[0].data["nested"]["index"] = "poison"
    assert store.events("abcdef")[0].data["nested"]["index"] == 0
    store.append_event(event(20))
    assert len(store.events("abcdef")) == 21
    stopped = len(weighed)
    assert store.events("abcdef") and len(weighed) == stopped, "an oversized log is not weighed again"
    # Replacing the log restarts the accounting: a small one is kept again.
    path = store.session_dir("abcdef") / "events.jsonl"
    replacement = path.with_suffix(".new")
    replacement.write_bytes(raw_event(7) + b"\n")
    os.replace(replacement, path)
    assert [e.event_id for e in store.events("abcdef")] == ["7"]
    assert len(weighed) > stopped and store._event_cache.metrics()["entries"] == 1
    # What the cache does keep is still handed out as a copy.
    assert len(copies) == 1


def test_a_kept_log_is_weighed_by_what_was_appended(tmp_path, monkeypatch):
    store = FileAgentSessionStore(tmp_path)
    for index in range(30):
        store.append_event(event(index))
    weighed, copies = _count_weighing_and_copies(monkeypatch)

    assert len(store.events("abcdef")) == 30
    assert len(weighed) == 30 and len(copies) == 30
    kept = store._event_cache.metrics()["cached_bytes"]

    weighed.clear()
    copies.clear()
    store.append_event(event(30))
    assert len(store.events("abcdef")) == 31
    assert len(weighed) == 1, "only the new record is walked, not the 30 already kept"
    assert store._event_cache.metrics()["cached_bytes"] > kept
    # An unchanged log is a hit: nothing is parsed or weighed, and every body is copied for the caller.
    weighed.clear()
    copies.clear()
    assert len(store.events("abcdef")) == 31
    assert weighed == [] and len(copies) == 31


def test_log_that_outgrows_the_cache_is_dropped_not_served_stale(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    store._event_cache = EventParseCache(max_bytes=6000)
    store.append_event(event(0))
    assert len(store.events("abcdef")) == 1 and store._event_cache.metrics()["entries"] == 1
    for index in range(1, 40):
        store.append_event(event(index))
    grown = store.events("abcdef")
    assert [e.event_id for e in grown] == [str(index) for index in range(40)]
    assert store._event_cache.metrics()["entries"] == 0
    grown[3].data["nested"]["index"] = "poison"
    assert store.events("abcdef")[3].data["nested"]["index"] == 3
