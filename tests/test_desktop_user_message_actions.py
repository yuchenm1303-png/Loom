from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop import TranscriptEntry, TranscriptView
from app.desktop.user_message_actions import UserMessageShell


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def test_user_message_renders_external_copy_share_edit_actions(qt_app):
    view = TranscriptView()
    view.resize(900, 500)
    view.show()
    entry = TranscriptEntry(
        key="user:1",
        kind="user",
        text="消息都生成完了，他们还在后面。",
        streaming=False,
    )

    view.render([entry])
    qt_app.processEvents()

    shell = view._widgets[entry.key]
    assert isinstance(shell, UserMessageShell)
    assert shell.message._text == entry.text
    assert shell.actions.isVisible()
    assert shell.actions.copy_button.toolTip() == "Copy message"
    assert shell.actions.share_button.toolTip() == "Copy for sharing"
    assert shell.actions.edit_button.toolTip() == "Edit in composer"

    # Actions are a separate row below the bubble rather than being painted
    # inside the purple message surface.
    layout = shell.layout()
    assert layout.itemAt(0).widget() is shell.message
    assert layout.itemAt(1).widget() is shell.actions
    assert layout.spacing() == 3

    view.close()


def test_copy_action_uses_exact_user_message_text(qt_app):
    shell = UserMessageShell()
    entry = TranscriptEntry(
        key="user:copy",
        kind="user",
        text="保留原始用户消息，不做改写。",
        streaming=False,
    )
    shell.set_entry(entry)
    shell.actions.copy_button.click()
    qt_app.processEvents()

    assert QApplication.clipboard().text() == entry.text
    assert shell.actions.copy_button.property("success") is True
    shell.close()


def test_user_shell_preserves_transcript_plain_text(qt_app):
    view = TranscriptView()
    view.render(
        [
            TranscriptEntry(key="u", kind="user", text="用户问题"),
            TranscriptEntry(key="a", kind="assistant", text="助手回答"),
        ]
    )
    qt_app.processEvents()

    assert view.toPlainText() == "用户问题\n\n助手回答"
    view.close()
