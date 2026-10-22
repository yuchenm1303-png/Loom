"""The small state stores keep one SQLite connection open instead of opening one per call.

On a WAL database the last connection to close checkpoints and deletes the -wal and -shm files and the next one makes
them again: 8-10 ms for a call that reads a row, a dozen times a step. The stores take their own lock around every use,
which is what makes one connection per store safe.
"""
from __future__ import annotations

import gc
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.agent_runtime.agent_graph import AgentGraphStore
from app.agent_runtime.durable_state import DurableThreadStateStore, GoalStatus
from app.agent_runtime.kept_connection import KeptConnection
from app.agent_runtime.memory_store import MemoryStore


@pytest.fixture()
def connects(monkeypatch):
    opened: list[str] = []
    real = sqlite3.connect

    def recording(database, *args, **kwargs):
        opened.append(str(database))
        return real(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", recording)
    return opened


def test_many_calls_share_the_connection_the_store_opened(tmp_path, connects):
    graph = AgentGraphStore(tmp_path)
    graph.add_child(parent_session_id="parent", child_session_id="child")
    first = len(connects)
    for _ in range(40):
        assert graph.get("child").parent_session_id == "parent"
        assert graph.get("nobody") is None
        graph.list_tree("parent")
    assert len(connects) == first == 1


def test_each_store_keeps_its_own_connection_and_they_see_each_others_writes(tmp_path):
    durable, graph, memory = DurableThreadStateStore(tmp_path), AgentGraphStore(tmp_path), MemoryStore(tmp_path)
    durable.set_goal("s1", "ship it")
    graph.add_child(parent_session_id="s1", child_session_id="s2")
    # A second store object on the same file reads what the first wrote, straight away.
    assert DurableThreadStateStore(tmp_path).get_goal("s1").objective == "ship it"
    assert AgentGraphStore(tmp_path).get("s2").parent_session_id == "s1"
    assert memory.counts(workspace=tmp_path)["total"] == 0


def test_a_write_by_another_process_is_seen_by_the_kept_connection(tmp_path):
    store = DurableThreadStateStore(tmp_path)
    assert store.get_goal("s1") is None
    script = (
        "import sys; from app.agent_runtime.durable_state import DurableThreadStateStore; "
        "DurableThreadStateStore(sys.argv[1]).set_goal('s1', 'from another process')"
    )
    repo = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=repo, check=True, timeout=60,
                   env={**os.environ, "PYTHONPATH": str(repo) + os.pathsep + os.environ.get("PYTHONPATH", "")})
    assert store.get_goal("s1").objective == "from another process"
    store.set_goal_status("s1", GoalStatus.PAUSED)
    assert DurableThreadStateStore(tmp_path).get_goal("s1").status is GoalStatus.PAUSED


def test_the_durable_store_keeps_foreign_keys_on(tmp_path):
    store = DurableThreadStateStore(tmp_path)
    with store._lock, store._connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert connection.row_factory is sqlite3.Row


def test_threads_take_turns_on_the_one_connection(tmp_path):
    graph = AgentGraphStore(tmp_path)
    failures: list[BaseException] = []

    def work(worker: int) -> None:
        try:
            for index in range(25):
                child = f"w{worker}-c{index}"
                graph.add_child(parent_session_id="root", child_session_id=child)
                assert graph.get(child).root_session_id == "root"
                graph.list_children("root")
        except BaseException as exc:  # noqa: BLE001 - reported below
            failures.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert not failures, failures
    assert len(graph.sub_agent_session_ids()) == 150


def test_a_failed_block_leaves_no_transaction_open_on_the_kept_connection(tmp_path):
    store = DurableThreadStateStore(tmp_path)
    with pytest.raises(RuntimeError):
        with store._lock, store._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO thread_goals(session_id, objective, status, tokens_used, created_at, updated_at)"
                               " VALUES ('x', 'half done', 'active', 0, 'now', 'now')")
            raise RuntimeError("boom")
    assert store.get_goal("x") is None
    store.set_goal("y", "next write works")  # would fail with "within a transaction" if one were left open
    assert store.get_goal("y").objective == "next write works"


def test_a_block_that_forgets_to_commit_does_not_leak_into_the_next_one(tmp_path):
    store = DurableThreadStateStore(tmp_path)
    with store._lock, store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO thread_goals(session_id, objective, status, tokens_used, created_at, updated_at)"
                           " VALUES ('kept', 'committed on exit', 'active', 0, 'now', 'now')")
    assert store.get_goal("kept").objective == "committed on exit"  # what `with connection:` always did
    with store._lock, store._connect() as connection:
        assert not connection.in_transaction


def test_a_use_inside_a_use_gets_a_connection_of_its_own(tmp_path):
    """Nothing nests today; if something did, the inner block must not commit the outer one's work."""
    store = DurableThreadStateStore(tmp_path)
    with pytest.raises(RuntimeError):
        with store._lock, store._connect() as outer:
            outer.execute("BEGIN IMMEDIATE")
            outer.execute("INSERT INTO thread_goals(session_id, objective, status, tokens_used, created_at, updated_at)"
                          " VALUES ('outer', 'not yet', 'active', 0, 'now', 'now')")
            with store._lock, store._connect() as inner:
                assert inner is not outer
                assert inner.execute("SELECT COUNT(*) FROM thread_goals WHERE session_id = 'outer'").fetchone()[0] == 0
            assert outer.in_transaction  # the inner block did not end the outer one
            raise RuntimeError("roll the outer block back")
    assert store.get_goal("outer") is None


def test_closing_releases_the_handles_and_the_next_use_opens_again(tmp_path, connects):
    store = AgentGraphStore(tmp_path)
    store.add_child(parent_session_id="p", child_session_id="c")
    kept = store._kept._box
    assert len(kept) == 1
    store.close_connection()
    assert kept == []
    before = len(connects)
    assert store.get("c").parent_session_id == "p"
    assert len(connects) == before + 1 and len(kept) == 1


def test_a_store_that_is_dropped_releases_its_connection(tmp_path):
    store = AgentGraphStore(tmp_path)
    store.add_child(parent_session_id="p", child_session_id="c")
    kept = store._kept._box
    assert len(kept) == 1
    del store
    gc.collect()
    assert kept == []
    # nothing holds the files any more: this is what keeps temporary directories deletable on Windows
    for name in ("state.db", "state.db-wal", "state.db-shm"):
        path = tmp_path / name
        if path.exists():
            path.unlink()


def test_the_kept_connection_closes_even_if_closing_it_fails(tmp_path):
    kept = KeptConnection(tmp_path / "x.db")
    with kept.use() as connection:
        connection.execute("CREATE TABLE t (a)")

    class Stubborn:
        def close(self):
            raise sqlite3.OperationalError("unable to close due to unfinalized statements")

    kept._box.insert(0, Stubborn())  # a second, broken handle in front of the real one
    kept.close()  # must not raise, and must still close the real one
    assert kept._box == []
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")  # closed
    with kept.use() as again:
        assert again.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_runtimes_release_the_stores_they_made_when_they_close(tmp_path):
    from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy, ToolRegistry

    class Silent:
        def execute_chat(self, profile, request):
            raise AssertionError("no model call expected")

    runtime = AgentRuntime(platform=Silent(), store=FileAgentSessionStore(tmp_path), tools=ToolRegistry(()),
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    session = runtime.create_session("agent.fast")
    runtime.get_goal(session.session_id)
    runtime.agent_graph.get(session.session_id)
    runtime.memory_store.counts(workspace=tmp_path)
    boxes = [runtime.durable_state._kept._box, runtime.agent_graph._kept._box, runtime.memory_store._kept._box]
    assert all(len(box) == 1 for box in boxes)
    runtime.close()
    assert all(box == [] for box in boxes)
    # and a late read after closing still works
    assert runtime.get_goal(session.session_id) is None
    runtime.close()


def test_a_transaction_that_could_not_end_is_rolled_back_rather_than_kept_open(tmp_path, monkeypatch):
    """If the commit itself fails (a busy database), the connection that stays open must not stay in a transaction."""
    events: list[str] = []

    class Failing:
        in_transaction = True

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            events.append("commit failed")
            raise sqlite3.OperationalError("database is locked")

        def rollback(self):
            events.append("rolled back")
            self.in_transaction = False

        def close(self):
            events.append("closed")

    kept = KeptConnection(tmp_path / "x.db")
    monkeypatch.setattr(kept, "_open", lambda: Failing())
    with pytest.raises(sqlite3.OperationalError):
        with kept.use():
            pass
    assert events == ["commit failed", "rolled back"]
    assert kept._busy is False  # and the next use is not mistaken for a nested one
