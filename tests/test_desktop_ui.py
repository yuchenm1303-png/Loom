from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.desktop_ui import LoomDesktopWindow, ThreadListItemWidget
from app.desktop.widgets import ActivityCard, MessageWidget, ThreadGroupHeader
from app.desktop.widgets import TranscriptView
from app.desktop.window import COMPOSER_MIN_HEIGHT, THREAD_ROLE


class FakeClient:
    """Minimal App Server stand-in speaking the same shapes as the real one."""

    def __init__(self, workspace) -> None:
        self.workspace = str(workspace)
        self.notification_listener = None
        self.stderr_listener = None
        self.exit_listener = None
        self.closed = False
        self.approvals = []
        self.turns = []
        self.requests = []
        self.reads = 0
        self.fail_turn = False

    # -- subscriptions ----------------------------------------------------

    def subscribe_notifications(self, listener):
        self.notification_listener = listener

    def subscribe_stderr(self, listener):
        self.stderr_listener = listener

    def subscribe_exit(self, listener):
        self.exit_listener = listener

    # -- protocol ---------------------------------------------------------

    def _record(self, *, archived=False):
        return {
            "id": "thread-archived" if archived else "thread-1",
            "title": "Archived work" if archived else "Inspect project",
            "workspace": self.workspace,
            "permissionMode": "workspace",
            "status": "completed",
            "currentTurnId": "turn-1",
            "archived": archived,
            "usage": {"inputTokens": 4, "outputTokens": 3, "totalTokens": 7},
        }

    def thread_list(self, *, limit=100):
        assert limit >= 1
        return {
            "threads": [self._record()],
            "counts": {"active": 1, "archived": 1, "all": 2},
        }

    def request(self, method, params):
        self.requests.append((method, params))
        if method == "thread/list":
            if params.get("view") == "archived":
                return {
                    "threads": [self._record(archived=True)],
                    "counts": {"active": 1, "archived": 1, "all": 2},
                }
            return self.thread_list(limit=params.get("limit", 100))
        if method == "thread/rename":
            record = dict(self._record(), title=params["title"])
            return {"thread": record}
        if method == "thread/archive":
            return {"thread": dict(self._record(), archived=params["archived"])}
        if method == "thread/delete":
            return {"threadId": params["threadId"]}
        raise AssertionError(f"unexpected request: {method}")

    def thread_read(self, thread_id):
        self.reads += 1
        archived = thread_id == "thread-archived"
        return {
            "thread": self._record(archived=archived),
            "messages": [],
            "pendingApproval": None,
            "events": [
                {
                    "eventId": "evt-1",
                    "threadId": thread_id,
                    "turnId": "turn-1",
                    "kind": "process_started",
                    "createdAt": "2026-09-05T12:00:00+00:00",
                    "data": {"process_id": "proc-1"},
                },
                {
                    "eventId": "evt-2",
                    "threadId": thread_id,
                    "turnId": "turn-1",
                    "kind": "turn_diff_updated",
                    "createdAt": "2026-09-05T12:00:01+00:00",
                    "data": {"paths": ["demo.txt"]},
                },
            ],
            "turns": [
                {
                    "id": "turn-1",
                    "threadId": thread_id,
                    "status": "completed",
                    "items": [
                        {
                            "id": "user:1",
                            "type": "user_message",
                            "status": "completed",
                            "text": "Inspect the repository",
                        },
                        {
                            "id": "process:proc-1",
                            "type": "process",
                            "status": "completed",
                            "processId": "proc-1",
                            "argv": ["python", "-V"],
                            "cwd": self.workspace,
                            "stdout": "Python 3.12.10\n",
                            "stderr": "",
                            "sandbox": {"enforced": False, "backend": "none"},
                        },
                        {
                            "id": "diff:evt-2",
                            "type": "file_edit",
                            "status": "completed",
                            "paths": ["demo.txt"],
                            "diff": "+hello\n",
                            "truncated": False,
                        },
                        {
                            "id": "tool:browser-1",
                            "type": "tool_call",
                            "status": "completed",
                            "toolName": "browser_navigate",
                            "arguments": {"url": "https://example.com"},
                        },
                        {
                            "id": "tool:agent-1",
                            "type": "tool_call",
                            "status": "completed",
                            "toolName": "spawn_agent",
                            "arguments": {"task": "review"},
                        },
                        {
                            "id": "assistant:1",
                            "type": "assistant_message",
                            "status": "completed",
                            "text": "The repository is ready.",
                        },
                    ],
                }
            ],
            "finalText": "The repository is ready.",
            "error": "",
        }

    def thread_start(self, *, workspace, permission_mode):
        return {
            "thread": {
                "id": "thread-new",
                "title": "New thread",
                "workspace": str(workspace),
                "permissionMode": permission_mode,
                "status": "idle",
            }
        }

    def turn_start(self, thread_id, text):
        if self.fail_turn:
            raise RuntimeError("provider unavailable")
        self.turns.append((thread_id, text))
        return {"turn": {"id": "turn-live", "threadId": thread_id, "status": "starting"}}

    def turn_interrupt(self, thread_id, turn_id=None):
        return {"requested": True, "threadId": thread_id, "turnId": turn_id}

    def approval_respond(self, thread_id, call_id, *, approved):
        self.approvals.append((thread_id, call_id, approved))
        return {"accepted": True}

    def close(self):
        self.closed = True

    def emit(self, method, params):
        assert self.notification_listener is not None
        self.notification_listener(method, params)


def _wait_for(app, predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Qt desktop condition did not become true")


def _initialization(workspace, *, management=True):
    return {
        "protocolVersion": 1,
        "serverInfo": {"name": "loom-app-server", "version": "0.1.0"},
        "capabilities": {"providerStreaming": True, "threadManagement": management},
        "runtime": {
            "defaultWorkspace": str(workspace),
            "defaultPermissionMode": "workspace",
        },
    }


@pytest.fixture
def desktop(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = FakeClient(tmp_path)
    window = LoomDesktopWindow(
        client=client,
        initialization=_initialization(tmp_path),
        default_workspace=tmp_path,
        default_permission_mode="workspace",
    )
    window.show()
    _wait_for(app, lambda: window.current_thread_id == "thread-1")
    try:
        yield app, client, window
    finally:
        window.close()
        app.processEvents()


def _transcript_widgets(window):
    return [window.transcript._widgets[key] for key in window.transcript._order]


def test_thread_rehydrates_into_transcript_and_runtime_panels(desktop):
    _app, client, window = desktop

    assert "Inspect the repository" in window.transcript.toPlainText()
    assert "The repository is ready." in window.transcript.toPlainText()
    assert "python -V" in window.terminal_view.toPlainText()
    assert "demo.txt" in window.diff_view.toPlainText()
    assert "browser_navigate" in window.browser_view.toPlainText()
    assert "spawn_agent" in window.agents_view.toPlainText()
    assert "Process started" in window.activity_view.toPlainText()

    assert window.thread_title_label.text() == "Inspect project"
    assert window.permission_label.text() == "workspace"
    assert window.status_label.text() == "Completed"
    assert window.status_label.property("state") == "completed"
    assert window.sandbox_label.text() == "Not sandboxed · none"
    assert window.sandbox_label.property("state") == "unprotected"
    assert window.protocol_label.text() == "Connected"
    assert "Provider streaming · on" in window.protocol_label.toolTip()
    assert window.connection_dot.property("state") == "connected"
    assert window.thread_section_label.text() == "CHATS  1"
    assert client.closed is False


def test_tool_process_and_diff_items_render_inline_as_cards(desktop):
    _app, _client, window = desktop
    widgets = _transcript_widgets(window)

    kinds = [
        widget.kind if isinstance(widget, ActivityCard) else widget.role for widget in widgets
    ]
    assert kinds == ["user", "process", "diff", "tool", "tool", "assistant"]

    process_card = widgets[1]
    assert process_card.title_label.text() == "$ python -V"
    assert "Python 3.12.10" in process_card._body_text

    diff_card = widgets[2]
    assert diff_card.title_label.text() == "Edited 1 file"
    assert "+hello" in diff_card._body_text


def _thread_rows(window):
    return [
        window.thread_list.item(index)
        for index in range(window.thread_list.count())
        if isinstance(window.thread_list.item(index).data(THREAD_ROLE), dict)
    ]


def test_rows_are_grouped_by_project_and_size_themselves(desktop):
    _app, _client, window = desktop

    header = window.thread_list.item(0)
    assert header.data(THREAD_ROLE) is None
    assert isinstance(window.thread_list.itemWidget(header), ThreadGroupHeader)
    assert header.flags() == Qt.ItemFlag.NoItemFlags

    rows = _thread_rows(window)
    assert len(rows) == 1
    row = window.thread_list.itemWidget(rows[0])
    assert isinstance(row, ThreadListItemWidget)
    # The old client hard-coded 58/60px rows while later layers changed the row
    # contents, which left large gaps in the sidebar.
    assert rows[0].sizeHint().height() == max(52, row.sizeHint().height())


def test_rows_show_only_a_title_and_keep_the_detail_in_the_tooltip(desktop):
    _app, _client, window = desktop

    row = window.thread_list.itemWidget(_thread_rows(window)[0])
    assert row.title_label.text() == "Inspect project"
    # Completed is a resting state, so it earns no dot.
    assert row.status_dot.isVisible() is False
    assert "Completed" in row.toolTip()
    assert "7 tokens" in row.toolTip()


def test_searching_hides_a_project_heading_with_no_matches(desktop):
    app, _client, window = desktop

    # Groups name themselves, so the count row only earns its line while a
    # search is narrowing the list.
    assert window.thread_section_row.isVisible() is False

    window.thread_search.setText("nothing matches")
    app.processEvents()
    assert window.thread_list.item(0).isHidden() is True
    assert window.thread_section_row.isVisible() is True
    assert window.thread_section_label.text() == "CHATS  0/1"

    window.thread_search.clear()
    app.processEvents()
    assert window.thread_list.item(0).isHidden() is False
    assert window.thread_section_row.isVisible() is False


def test_the_composer_starts_compact_and_hides_idle_chrome(desktop):
    _app, _client, window = desktop

    # A tall empty box with a disabled Stop button and a "Ready" label reads as
    # clutter; none of it says anything until a turn is running.
    assert window.composer.height() == COMPOSER_MIN_HEIGHT
    assert window.stop_button.isVisible() is False
    assert window.composer_state_label.isVisible() is False
    # This thread has spent tokens, so the counter has something to report.
    assert window.usage_label.text() == "7 tokens"


def test_an_unused_conversation_shows_no_token_counter(desktop):
    app, _client, window = desktop

    window.new_thread_in_current_workspace()
    app.processEvents()
    assert window.usage_label.isVisible() is False
    assert window.usage_label.text() == ""


def test_the_composer_shares_the_transcript_measure(desktop):
    app, _client, window = desktop
    window.resize(1990, 1180)
    app.processEvents()

    # Laying it out with an alignment flag gave it its sizeHint width, which
    # left it visibly narrower than the conversation above it.
    assert window.composer_frame.width() == TranscriptView.MAX_CONTENT_WIDTH + 18


def test_streaming_updates_only_the_live_message_widget(desktop):
    app, client, window = desktop

    before = {key: id(widget) for key, widget in window.transcript._widgets.items()}

    client.emit(
        "turn/started",
        {"threadId": "thread-1", "turn": {"id": "turn-live", "status": "running"}},
    )
    client.emit(
        "item/started",
        {
            "item": {
                "id": "assistant:live",
                "threadId": "thread-1",
                "turnId": "turn-live",
                "type": "assistant_message",
                "status": "streaming",
            }
        },
    )
    for chunk in ("Live ", "provider ", "chunk"):
        client.emit(
            "item/delta",
            {
                "threadId": "thread-1",
                "itemId": "assistant:live",
                "delta": {"text": chunk},
            },
        )
    _wait_for(app, lambda: "Live provider chunk" in window.transcript.toPlainText())

    # Every widget that existed before streaming is the same object afterwards.
    for key, identity in before.items():
        assert id(window.transcript._widgets[key]) == identity

    live = window.transcript._widgets["assistant:live"]
    assert isinstance(live, MessageWidget)
    assert live.stream_badge.isVisible() is True
    assert window.status_label.text() == "Running"
    assert window.composer_state_label.text() == "Loom is working"
    assert window.stop_button.isEnabled() is True


def test_approval_card_answers_with_the_call_it_displayed(desktop):
    app, client, window = desktop

    client.emit(
        "approval/requested",
        {
            "threadId": "thread-1",
            "approval": {
                "callId": "call-approval",
                "toolName": "run_workspace_command",
                "arguments": {"argv": ["python", "-V"]},
                "effect": "sensitive",
                "reason": "process execution requires approval",
            },
        },
    )
    app.processEvents()

    assert window.approval_frame.isVisible() is True
    assert "run_workspace_command" in window.approval_title.text()
    assert "sensitive" in window.approval_details.text()
    assert "python" in window.approval_frame.arguments_view.toPlainText()
    assert window.status_label.text() == "Waiting Approval"
    assert window.composer_state_label.text() == "Waiting for approval"

    window.approval_frame.allow_button.click()
    _wait_for(app, lambda: client.approvals)
    assert client.approvals == [("thread-1", "call-approval", True)]


def test_rpc_failures_surface_in_the_banner_not_a_modal_dialog(desktop):
    app, client, window = desktop
    client.fail_turn = True

    window.composer.setPlainText("do the thing")
    window.send_prompt()
    _wait_for(app, lambda: window.banner.isVisible())

    assert "provider unavailable" in window.banner.label.text()
    window.banner.dismiss()
    assert window.banner.isVisible() is False


def test_empty_conversation_shows_the_starting_prompts(desktop):
    app, _client, window = desktop

    window.state.reset()
    window._render_transcript()
    app.processEvents()
    assert window.empty_state.isVisible() is True
    assert window.transcript.isVisible() is False

    window.empty_state.promptChosen.emit("Inspect this project")
    assert window.composer.toPlainText() == "Inspect this project"


def test_panel_toggles_hide_and_restore_both_side_panels(desktop):
    app, _client, window = desktop

    window.toggle_sidebar()
    window.toggle_runtime()
    app.processEvents()
    assert window.sidebar_panel.isVisible() is False
    assert window.activity_panel.isVisible() is False

    window.toggle_sidebar()
    window.toggle_runtime()
    app.processEvents()
    assert window.sidebar_panel.isVisible() is True
    assert window.activity_panel.isVisible() is True


def test_a_new_conversation_stays_a_draft_until_it_is_sent(desktop):
    app, client, window = desktop
    rows_before = len(_thread_rows(window))

    window.new_thread_in_current_workspace()
    app.processEvents()
    # Clicking "New thread" must not create anything on the server yet; the old
    # client did, which is why the library filled up with empty conversations.
    assert window.current_thread_id == ""
    assert window.empty_state.isVisible() is True
    assert len(_thread_rows(window)) == rows_before

    window.composer.setPlainText("first message")
    window.send_prompt()
    _wait_for(app, lambda: client.turns)

    assert client.turns == [("thread-new", "first message")]
    assert window._draft_workspace is None


def test_startup_with_no_threads_opens_a_draft_instead_of_creating_one(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = FakeClient(tmp_path)
    client.thread_list = lambda *, limit=100: {"threads": [], "counts": {"active": 0}}
    window = LoomDesktopWindow(
        client=client,
        initialization=_initialization(tmp_path),
        default_workspace=tmp_path,
        default_permission_mode="workspace",
    )
    window.show()
    _wait_for(app, lambda: window._draft_workspace is not None)
    try:
        assert window.current_thread_id == ""
        assert window.thread_title_label.text() == "New conversation"
        assert window.empty_state.isVisible() is True
    finally:
        window.close()
        app.processEvents()


def test_conversation_library_search_archive_view_and_read_only_state(desktop):
    app, client, window = desktop

    assert window.thread_search.placeholderText() == "Search conversations"
    assert window.archive_view_button.isEnabled() is True
    assert window.thread_actions_button.isEnabled() is True

    window.thread_search.setText("nothing matches")
    app.processEvents()
    assert window.thread_section_label.text() == "CHATS  0/1"
    window.thread_search.clear()
    app.processEvents()

    window.archive_view_button.setChecked(True)
    window._toggle_archive_view(True)
    _wait_for(app, lambda: window.thread_section_label.text().startswith("ARCHIVED"))
    assert any(
        method == "thread/list" and params.get("view") == "archived"
        for method, params in client.requests
    )

    _wait_for(app, lambda: window.current_thread_id == "thread-archived")
    assert window.composer.isReadOnly() is True
    assert window.send_button.isEnabled() is False
    assert window.composer_state_label.text() == "Archived · read-only"


def test_renaming_a_conversation_updates_the_header(desktop):
    app, client, window = desktop

    window.rpc.submit(
        "thread-rename:thread-1",
        lambda: window._thread_action(
            "thread/rename", {"threadId": "thread-1", "title": "Renamed"}
        ),
    )
    _wait_for(app, lambda: window.thread_title_label.text() == "Renamed")
    assert ("thread/rename", {"threadId": "thread-1", "title": "Renamed"}) in client.requests


def test_item_bursts_are_reconciled_once_rather_than_per_item(desktop):
    app, client, window = desktop
    reads_before = client.reads

    for index in range(8):
        client.emit(
            "item/completed",
            {
                "threadId": "thread-1",
                "item": {
                    "id": f"tool:burst-{index}",
                    "type": "tool_call",
                    "status": "completed",
                    "toolName": "read_file",
                },
            },
        )

    _wait_for(app, lambda: client.reads > reads_before)
    # Let any further scheduled reads land before counting.
    for _ in range(40):
        app.processEvents()
        time.sleep(0.01)

    # The old client issued one full thread/read per completed item.
    assert client.reads == reads_before + 1


def test_server_exit_marks_the_connection_and_disables_sending(desktop):
    app, client, window = desktop

    assert client.exit_listener is not None
    client.exit_listener("Loom App Server exited (exit code 1)")
    _wait_for(app, lambda: window.connection_dot.property("state") == "disconnected")

    assert window.protocol_label.text() == "App Server · stopped"
    assert window.send_button.isEnabled() is False
    assert window.stop_button.isEnabled() is False
    assert "exit code 1" in window.banner.label.text()


def test_closing_the_window_shuts_down_the_client(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = FakeClient(tmp_path)
    window = LoomDesktopWindow(
        client=client,
        initialization=_initialization(tmp_path),
        default_workspace=tmp_path,
        default_permission_mode="workspace",
    )
    window.show()
    _wait_for(app, lambda: window.current_thread_id == "thread-1")
    window.close()
    app.processEvents()
    assert client.closed is True
