"""A transcript window is read through an index of the log's line headers, and says exactly what the whole view says.

``thread/read`` for a conversation asks for its newest turns. Parsing the whole log to find them cost 100-200 ms on a long
session; the index (``event_index.py``) finds the window by line headers and only the window's own lines are parsed. The
answers must not change by a byte, whatever shape the log has.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.agent_runtime.storage as storage_module
from app.agent_runtime import AgentEventKind as E
from app.agent_runtime import PendingToolApproval, PermissionMode, ToolEffect
from app.agent_runtime.contracts import AgentStatus
from app.agent_runtime.storage import FileAgentSessionStore

from test_app_server_context_budget import _RECORDED_REQUEST, _build_service
from test_thread_open_reads import _Spy

TURNS = 7


def _seed(service, runtime, workspace: Path, turns: int = TURNS) -> str:
    thread_id = service.thread_start(
        {"workspace": str(workspace), "permissionMode": "workspace", "title": "Index"}
    )["thread"]["id"]
    session = runtime.store.load(thread_id)
    for number in range(1, turns + 1):
        _turn(runtime, session, number)
    return thread_id


def _turn(runtime, session, number: int) -> None:
    session.current_turn_id = f"turn-{number}"
    runtime._record(session, E.TURN_STARTED, data={"source": "user"})
    runtime._record(session, E.USER_MESSAGE, data={"text": f"question {number}"})
    runtime._record(session, E.MODEL_REQUESTED, data={**_RECORDED_REQUEST, "request_layout": {"sections": list(range(30))}})
    runtime._record(session, E.MODEL_RESPONSE, data={"text": f"working {number}", "usage": {"total_tokens": 5}, "step_id": f"s{number}"})
    runtime._record(session, E.TOOL_REQUESTED, data={"call_id": f"c{number}", "tool": "read", "arguments": {"path": "a"}})
    runtime._record(session, E.TOOL_COMPLETED, data={"call_id": f"c{number}", "tool": "read", "ok": True, "content": "body" * 20})
    runtime._record(session, E.MODEL_REQUESTED, data={**_RECORDED_REQUEST, "message_count": 9 + number})
    runtime._record(session, E.TURN_COMPLETED, data={"text": f"answer {number}", "final_step_id": f"s{number}"})


def _answers(service, store, thread_id: str, params: dict):
    """The same read through the indexed window and through the whole view, or the errors they raise."""
    params = {"threadId": thread_id, "presentationOnly": True, **params}
    outcomes = []
    for hidden in ((), ("presentation_window",)):
        service.store = _Spy(store, hide=hidden) if hidden else store
        try:
            outcomes.append(("ok", json.dumps(service.thread_read(params), sort_keys=True)))
        except Exception as exc:  # noqa: BLE001 - the type and message are what is compared
            outcomes.append(("error", f"{type(exc).__name__}: {exc}"))
    service.store = store
    return outcomes


def _same(service, store, thread_id: str, cursor_turns: bool = True) -> None:
    params = [{"turnLimit": limit} for limit in (1, 2, 3, 5, TURNS, 100)]
    first = service.thread_read({"threadId": thread_id, "presentationOnly": True, "turnLimit": 2})
    if cursor_turns and first.get("oldestTurnId"):
        params += [{"turnLimit": 2, "beforeTurnId": first["oldestTurnId"]}, {"turnLimit": 100, "beforeTurnId": first["oldestTurnId"]}]
    params += [{"turnLimit": 3, "beforeTurnId": "no-such-turn"}, {"turnLimit": 3, "beforeTurnId": "   "}]
    for one in params:
        window, whole = _answers(service, store, thread_id, one)
        assert window == whole, one


def test_the_window_answers_what_the_whole_view_answers(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        thread_id = _seed(service, runtime, workspace)
        _same(service, runtime.store, thread_id)
        latest = service.thread_read({"threadId": thread_id, "presentationOnly": True, "turnLimit": 3})
        assert [turn["id"] for turn in latest["turns"]] == ["turn-5", "turn-6", "turn-7"]
        assert latest["hasMoreTurns"] is True and latest["finalText"] == "answer 7"
    finally:
        runtime.close()


def test_events_appended_after_the_first_read_are_seen_without_indexing_the_log_again(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        store = runtime.store
        thread_id = _seed(service, runtime, workspace, turns=4)
        _same(service, store, thread_id)
        indexes = store._log_indexes
        built = indexes.built
        session = store.load(thread_id)
        _turn(runtime, session, 5)
        _same(service, store, thread_id)
        runtime._record(session, E.TURN_STARTED, data={"source": "user"})
        _same(service, store, thread_id)
        assert indexes.built == built and indexes.extended >= 2
    finally:
        runtime.close()


def test_a_late_event_of_an_older_turn_belongs_to_that_turn(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        store = runtime.store
        thread_id = _seed(service, runtime, workspace, turns=4)
        session = store.load(thread_id)
        session.current_turn_id = "turn-2"
        runtime._record(session, E.PROCESS_OUTPUT, data={"process_id": "late", "stdout": "arrived after turn 4"})
        session.current_turn_id = "turn-4"
        _same(service, store, thread_id)
        three = service.thread_read({"threadId": thread_id, "presentationOnly": True, "turnLimit": 3})
        one = service.thread_read({"threadId": thread_id, "presentationOnly": True, "turnLimit": 1})
        assert any(item.get("id") == "process:late" for turn in three["turns"] for item in turn["items"])
        assert not any(item.get("id") == "process:late" for turn in one["turns"] for item in turn["items"])
    finally:
        runtime.close()


def test_a_log_that_was_replaced_is_indexed_again(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        store = runtime.store
        thread_id = _seed(service, runtime, workspace, turns=5)
        _same(service, store, thread_id)
        path = store.session_dir(thread_id) / "events.jsonl"
        original = path.read_bytes()
        lines = original.split(b"\n")
        path.write_bytes(b"\n".join(lines[: len(lines) // 2]) + b"\n")  # shorter: it was truncated
        _same(service, store, thread_id)
        path.write_bytes(original)  # and the same bytes again
        _same(service, store, thread_id)
        first = json.dumps({"event_id": "e-first", "session_id": thread_id, "turn_id": "turn-0", "kind": "user_message",
                            "created_at": "2026-10-21T00:00:00.000+00:00", "data": {"text": "written before the rest"}})
        path.write_bytes(first.encode() + b"\n" + original)  # longer, and every offset the index holds is now wrong
        _same(service, store, thread_id)
        assert service.thread_read({"threadId": thread_id, "presentationOnly": True, "turnLimit": 100})["turns"][0]["id"] == "turn-0"
    finally:
        runtime.close()


def test_a_last_line_without_its_newline_is_left_to_the_whole_view(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        store = runtime.store
        thread_id = _seed(service, runtime, workspace, turns=3)
        path = store.session_dir(thread_id) / "events.jsonl"
        line = json.dumps({"event_id": "e-last", "session_id": thread_id, "turn_id": "turn-9", "kind": "user_message",
                           "created_at": "2026-10-22T00:00:00.000+00:00", "data": {"text": "no newline yet"}})
        with path.open("ab") as handle:
            handle.write(line.encode())
        assert store.presentation_window(thread_id, limit=3) is None
        _same(service, store, thread_id)
        latest = service.thread_read({"threadId": thread_id, "presentationOnly": True, "turnLimit": 1})
        assert [turn["id"] for turn in latest["turns"]] == ["turn-9"]
        with path.open("ab") as handle:
            handle.write(b"\n")
        assert store.presentation_window(thread_id, limit=3) is not None
        _same(service, store, thread_id)
    finally:
        runtime.close()


def test_lines_that_do_not_have_the_usual_header_are_indexed_by_what_they_say(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        store = runtime.store
        thread_id = _seed(service, runtime, workspace, turns=3)
        path = store.session_dir(thread_id) / "events.jsonl"
        odd = [
            {"kind": "user_message", "turn_id": "turn-8", "event_id": "e-odd-1", "session_id": thread_id, "created_at": "2026-10-22T00:00:00.000+00:00", "data": {"text": "other key order"}},
            {"event_id": "e-odd-2", "session_id": thread_id, "turn_id": "turn-8", "kind": "model_response", "created_at": "2026-10-22T00:00:01.000+00:00", "data": {"text": "\"quoted\" 中文", "step_id": "s8"}},
        ]
        with path.open("ab") as handle:
            handle.write(b"\n\n   \n")
            for payload in odd:
                handle.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        _same(service, store, thread_id)
        latest = service.thread_read({"threadId": thread_id, "presentationOnly": True, "turnLimit": 1})
        assert [turn["id"] for turn in latest["turns"]] == ["turn-8"] and len(latest["turns"][0]["items"]) == 2
    finally:
        runtime.close()


def test_the_pending_approval_and_the_final_text_come_from_the_same_events(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        store = runtime.store
        thread_id = _seed(service, runtime, workspace, turns=4)
        session = store.load(thread_id)
        session.current_turn_id = "turn-5"
        runtime._record(session, E.TURN_STARTED, data={"source": "user"})
        runtime._record(session, E.TOOL_REQUESTED, data={"call_id": "c-ask", "tool": "write", "arguments": {"path": "a.txt"}})
        runtime._record(session, E.TOOL_APPROVAL_REQUIRED, data={"call_id": "c-ask", "tool": "write", "arguments": {"path": "a.txt"}, "effect": "mutating", "reason": "asks"})
        session = store.load(thread_id)
        session.current_turn_id = "turn-5"
        session.status = AgentStatus.WAITING_APPROVAL
        session.pending_approval = PendingToolApproval(call_id="c-ask", tool_name="write", arguments={"path": "a.txt"}, effect=ToolEffect.MUTATING, reason="asks")
        store.save(session)
        _same(service, store, thread_id)
        read = service.thread_read({"threadId": thread_id, "presentationOnly": True, "turnLimit": 2})
        assert read["pendingApproval"]["callId"] == "c-ask" and read["pendingApproval"]["turnId"] == "turn-5"
        older = service.thread_read({"threadId": thread_id, "presentationOnly": True, "turnLimit": 2, "beforeTurnId": "turn-3"})
        assert older["pendingApproval"]["callId"] == "c-ask"  # the request lives in a turn outside this page
    finally:
        runtime.close()


def test_the_window_parses_its_own_lines_and_nothing_else(tmp_path, monkeypatch):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        store = runtime.store
        thread_id = _seed(service, runtime, workspace, turns=TURNS)
        parsed = []
        original = storage_module._presentation_line
        monkeypatch.setattr(storage_module, "_presentation_line", lambda line: parsed.append(line) or original(line))
        total = len((store.session_dir(thread_id) / "events.jsonl").read_bytes().splitlines())
        service.thread_read({"threadId": thread_id, "presentationOnly": True, "turnLimit": 1})
        assert 0 < len(parsed) <= 10 < total, (len(parsed), total)
        parsed.clear()
        service.thread_read({"threadId": thread_id, "presentationOnly": True, "turnLimit": 1})
        assert 0 < len(parsed) <= 10  # and again, with the index kept, no more than the window
    finally:
        runtime.close()


def test_loads_inside_a_read_scope_share_one_parse_until_the_session_is_saved(tmp_path, monkeypatch):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        store = runtime.store
        thread_id = _seed(service, runtime, workspace, turns=2)
        parses = []
        original = storage_module.session_from_dict
        monkeypatch.setattr(storage_module, "session_from_dict", lambda payload: parses.append(1) or original(payload))
        store.load(thread_id)
        store.load(thread_id)
        assert len(parses) == 2  # outside a scope every load reads the file
        parses.clear()
        with store.read_scope():
            first = store.load(thread_id)
            assert store.load(thread_id) is first and store.load(thread_id) is first
            assert len(parses) == 1
            with store.read_scope():  # nested scopes are the outer one
                assert store.load(thread_id) is first
            first.model = "changed-by-a-reader"
            store.save(first)
            second = store.load(thread_id)
            assert second is not first and second.model == "changed-by-a-reader" and len(parses) == 2
        assert store.load(thread_id) is not second  # the scope is over
    finally:
        runtime.close()


def test_a_read_scope_belongs_to_its_thread(tmp_path):
    import threading

    store = FileAgentSessionStore(tmp_path / "home")
    seen = []
    with store.read_scope():
        thread = threading.Thread(target=lambda: seen.append(getattr(store._scope, "loaded", "none")))
        thread.start()
        thread.join()
    assert seen == [None] or seen == ["none"]


def test_the_production_service_reads_a_thread_with_one_parse_of_the_session(tmp_path, monkeypatch):
    from app.app_server_browser_policy import BrowserPolicyLoomAppServerService

    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        store = runtime.store
        thread_id = _seed(service, runtime, workspace, turns=3)
        session = store.load(thread_id)
        session.model = "test-model"  # a thread that names its model is left as it is by a read (one without gets it saved)
        store.save(session)
        production = BrowserPolicyLoomAppServerService(
            runtime=runtime, store=store, model="test-model", default_workspace=workspace,
            default_permission_mode=PermissionMode.WORKSPACE,
        )
        parses = []
        original = storage_module.session_from_dict
        monkeypatch.setattr(storage_module, "session_from_dict", lambda payload: parses.append(1) or original(payload))
        read = production.thread_read({"threadId": thread_id, "presentationOnly": True, "turnLimit": 2})
        assert [turn["id"] for turn in read["turns"]] == ["turn-2", "turn-3"]
        assert len(parses) == 1, f"the layers of a thread read parsed the session {len(parses)} times"
        parses.clear()
        production.thread_read({"threadId": thread_id})  # the diagnostic read goes through the same scope
        assert len(parses) == 1
    finally:
        runtime.close()


def test_a_log_is_indexed_in_chunks_whatever_the_length_of_its_lines(tmp_path, monkeypatch):
    import app.agent_runtime.event_index as index_module

    monkeypatch.setattr(index_module, "_CHUNK", 600)  # shorter than most lines of the log
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        store = runtime.store
        thread_id = _seed(service, runtime, workspace, turns=4)
        _same(service, store, thread_id)
        session = store.load(thread_id)
        _turn(runtime, session, 5)  # and what is appended afterwards is read in chunks too
        _same(service, store, thread_id)
    finally:
        runtime.close()


def test_an_index_that_failed_half_way_is_not_used_again(tmp_path, monkeypatch):
    import app.agent_runtime.event_index as index_module

    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        store = runtime.store
        thread_id = _seed(service, runtime, workspace, turns=3)
        _same(service, store, thread_id)
        session = store.load(thread_id)
        _turn(runtime, session, 4)
        original = index_module.LogIndex._index
        calls = []

        def failing(self, raw, complete):
            original(self, raw, complete)
            calls.append(1)
            raise OSError("the disk went away")

        monkeypatch.setattr(index_module.LogIndex, "_index", failing)
        with pytest.raises(OSError):
            store.presentation_window(thread_id, limit=3)
        monkeypatch.setattr(index_module.LogIndex, "_index", original)
        assert calls
        _same(service, store, thread_id)  # indexed again from the start, not from a half-extended state
    finally:
        runtime.close()


def test_a_thread_without_a_log_answers_like_the_whole_view_including_a_cursor_that_names_nothing(tmp_path):
    service, runtime, workspace = _build_service(tmp_path, [])
    try:
        store = runtime.store
        thread_id = service.thread_start({"workspace": str(workspace), "permissionMode": "workspace", "title": "No log"})["thread"]["id"]
        log = store.session_dir(thread_id) / "events.jsonl"
        if log.exists():
            log.unlink()
        for params in ({"turnLimit": 5}, {"turnLimit": 5, "beforeTurnId": "turn-1"}, {"turnLimit": 1, "beforeTurnId": "   "}):
            window, whole = _answers(service, store, thread_id, params)
            assert window == whole, params
        window, _ = _answers(service, store, thread_id, {"turnLimit": 5, "beforeTurnId": "turn-1"})
        assert window[0] == "error" and "beforeTurnId" in window[1]
    finally:
        runtime.close()
