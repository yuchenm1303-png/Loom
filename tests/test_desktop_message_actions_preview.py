from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit

import app.desktop  # noqa: F401 - installs presentation hooks
from app.desktop.output_presentation import MessageWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_open_response_action_builds_a_read_only_preview(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    message = MessageWidget("assistant")
    message.set_text("A focused preview should contain this response.")
    message.set_streaming(False)
    message.show()

    message.message_actions.open_button.click()
    app.processEvents()

    dialogs = message.findChildren(QDialog)
    assert dialogs
    editor = dialogs[-1].findChild(QPlainTextEdit, "messagePreviewText")
    assert editor is not None
    assert editor.isReadOnly() is True
    assert "focused preview" in editor.toPlainText()

    dialogs[-1].close()
    message.close()
