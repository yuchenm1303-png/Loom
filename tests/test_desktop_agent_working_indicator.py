from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop import TranscriptEntry, TranscriptView
from app.desktop.agent_working_indicator import AgentWorkingIndicator, install_window


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_working_indicator_is_immediate_and_sits_after_user_message(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    view = TranscriptView()
    view.render([TranscriptEntry(key="u1", kind="user", text="你好")])

    view.set_agent_working(True)

    indicator = view.agent_working_indicator
    assert isinstance(indicator, AgentWorkingIndicator)
    assert indicator.active is True
    assert indicator.isHidden() is False
    assert indicator.label.text() == "Working…"
    assert view._layout.indexOf(view._widgets["u1"]) < view._layout.indexOf(indicator)
    assert view._layout.indexOf(indicator) == view._layout.count() - 2

    view.set_agent_working(False)
    assert indicator.active is False
    assert indicator.isHidden() is True
    view.close()


def test_working_indicator_stays_at_tail_when_tool_rows_arrive(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    view = TranscriptView()
    view.render([TranscriptEntry(key="u1", kind="user", text="检查磁盘")])
    view.set_agent_working(True)

    view.render(
        [
            TranscriptEntry(key="u1", kind="user", text="检查磁盘"),
            TranscriptEntry(
                key="tool1",
                kind="tool",
                item={"type": "tool_call", "toolName": "disk_status", "status": "running"},
                streaming=True,
            ),
        ]
    )

    indicator = view.agent_working_indicator
    assert indicator.active is True
    assert view._layout.indexOf(view._widgets["tool1"]) < view._layout.indexOf(indicator)
    assert view._layout.indexOf(indicator) == view._layout.count() - 2
    view.close()


def test_window_lifecycle_shows_then_hides_working_indicator():
    class Transcript:
        def __init__(self):
            self.states: list[bool] = []
            self.tail_calls = 0

        def set_agent_working(self, active: bool) -> None:
            self.states.append(bool(active))

        def scroll_to_tail(self) -> None:
            self.tail_calls += 1

    class Composer:
        def text(self) -> str:
            return ""

    class State:
        archived = False
        thread_id = "thread-1"

    class Window:
        def __init__(self):
            self.transcript = Transcript()
            self.composer_panel = Composer()
            self.state = State()
            self._draft_workspace = None

        def send_prompt(self, text: str = "") -> None:
            return None

        def _on_notification(self, method, params):
            return None

        def _on_rpc_error(self, tag: str, message: str):
            return None

        def interrupt_turn(self):
            return None

    install_window(Window)
    window = Window()

    window.send_prompt("hello")
    assert window.transcript.states[-1] is True
    assert window.transcript.tail_calls >= 1

    window._on_notification(
        "item/started",
        {"item": {"id": "tool-1", "type": "tool_call", "status": "running"}},
    )
    assert window.transcript.states[-1] is True

    window._on_notification(
        "item/started",
        {"item": {"id": "assistant-1", "type": "assistant_message", "status": "running"}},
    )
    assert window.transcript.states[-1] is False

    window.send_prompt("again")
    window._on_notification("approval/requested", {"approval": {"callId": "a1"}})
    assert window.transcript.states[-1] is False
