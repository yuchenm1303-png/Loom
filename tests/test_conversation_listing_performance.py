from __future__ import annotations

import sqlite3

import pytest

from app.agent_runtime.agent_graph import AgentGraphStore


def _count_connections(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every database the store opens while a listing runs."""

    opened: list[str] = []
    real_connect = sqlite3.connect

    def recording_connect(database, *args, **kwargs):
        opened.append(str(database))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", recording_connect)
    return opened


def test_sub_agent_session_ids_lists_every_child_in_one_query(tmp_path):
    store = AgentGraphStore(tmp_path)
    store.add_child(parent_session_id="parent", child_session_id="child-a")
    store.add_child(parent_session_id="parent", child_session_id="child-b")

    ids = store.sub_agent_session_ids()

    assert ids == frozenset({"child-a", "child-b"})


def test_sub_agent_session_ids_is_empty_for_a_fresh_store(tmp_path):
    store = AgentGraphStore(tmp_path)

    assert store.sub_agent_session_ids() == frozenset()


def test_sub_agent_session_ids_includes_closed_children(tmp_path):
    store = AgentGraphStore(tmp_path)
    store.add_child(parent_session_id="parent", child_session_id="child-a")
    store.add_child(parent_session_id="parent", child_session_id="child-b")
    store.close("child-b")

    ids = store.sub_agent_session_ids()

    # A closed child is still a sub-agent session, so it must stay hidden from
    # the top-level conversation library.
    assert ids == frozenset({"child-a", "child-b"})


def test_listing_a_large_library_opens_one_database_connection(tmp_path, monkeypatch):
    """The listing must not open a connection per conversation.

    Asking the agent graph about one session at a time made a conversation
    listing cost one SQLite connection per conversation, which is what made
    the Host busy while the sidebar polled.
    """

    store = AgentGraphStore(tmp_path)
    store.add_child(parent_session_id="parent", child_session_id="child-a")

    class RecordingStore:
        def __init__(self, root):
            self.root = root

        def load_overview(self, session_id):
            raise AssertionError("not reached")

    opened = _count_connections(monkeypatch)

    ids = store.sub_agent_session_ids()

    assert ids == frozenset({"child-a"})
    assert len(opened) == 1, f"expected one connection, opened {opened}"


def test_sub_agent_session_ids_opens_exactly_one_connection_per_call(tmp_path, monkeypatch):
    store = AgentGraphStore(tmp_path)
    for index in range(20):
        store.add_child(parent_session_id="parent", child_session_id=f"child-{index}")

    opened = _count_connections(monkeypatch)

    ids = store.sub_agent_session_ids()

    assert len(ids) == 20
    assert len(opened) == 1, f"expected one connection, opened {opened}"
