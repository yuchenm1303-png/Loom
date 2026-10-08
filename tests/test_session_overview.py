import json
from pathlib import Path

from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.session_overview import SessionOverviewCache
from app.app_server import _thread_record


def write_session(store, identity="aa", **updates):
    directory = store.session_dir(identity)
    directory.mkdir(exist_ok=True)
    payload = {
        "session_id": identity, "profile_id": "test", "workspace_dir": "workspace", "system_prompt": "Test prompt",
        "status": "completed", "updated_at": "2026-10-07T12:00:00Z",
        "messages": [{"role": "user", "content": "First question\nwith context"},
                     {"role": "assistant", "content": "Large answer" * 10000}],
        "request_context_frames": [{"text": "Private context" * 10000}],
        "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    }
    payload.update(updates)
    path = directory / "session.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_overview_matches_record_and_does_not_change_full_session(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    write_session(store)
    overview = store.load_overview("aa")
    full = store.load("aa")
    assert _thread_record(overview) == _thread_record(full)
    assert len(overview.messages) == 1
    assert not hasattr(overview, "request_context_frames")
    assert len(full.messages) == 2
    assert full.request_context_frames
    # Mutating an overview cannot affect later cache readers or runtime state.
    overview.messages.clear()
    assert store.load_overview("aa").usage.input_tokens == 10


def test_unchanged_list_read_skips_snapshot_io_but_replacement_invalidates(tmp_path, monkeypatch):
    store = FileAgentSessionStore(tmp_path)
    path = write_session(store)
    store.load_overview("aa")
    original_read = Path.read_text
    reads = []

    def counted_read(self, *args, **kwargs):
        if self == path:
            reads.append(self)
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read)
    for _ in range(20):
        assert store.load_overview("aa").status.value == "completed"
    assert reads == []
    replacement = path.with_suffix(".tmp")
    replacement.write_text(json.dumps({"session_id": "aa", "profile_id": "test", "workspace_dir": "workspace", "status": "running"}), encoding="utf-8")
    replacement.replace(path)
    assert store.load_overview("aa").status.value == "running"
    assert len(reads) == 1


def test_overview_titles_with_images_and_empty_user_messages(tmp_path):
    store = FileAgentSessionStore(tmp_path)
    write_session(store, messages=[{"role": "user", "content": " "},
        {"role": "user", "content": [{"type": "image", "image_url": "data:image/png;base64," + "x" * 100000}]}])
    assert _thread_record(store.load_overview("aa")) == _thread_record(store.load("aa"))
    assert store.load_overview("aa").messages[0].content == "[1 image attached]"


def test_overview_cache_is_bounded_and_deleted_files_are_not_served(tmp_path):
    cache = SessionOverviewCache(limit=2)
    paths = []
    for identity in ("aa", "bb", "cc"):
        path = tmp_path / f"{identity}.json"
        path.write_text(json.dumps({"session_id": identity}), encoding="utf-8")
        paths.append(path)
        cache.read(path)
    assert len(cache._entries) == 2
    paths[-1].unlink()
    import pytest
    with pytest.raises(FileNotFoundError):
        cache.read(paths[-1])
