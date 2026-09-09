from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import app.desktop  # noqa: F401 - installs message action presentation passes
from app.desktop.message_presentation import MessageWidget


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_completed_response_actions_share_one_vertical_rhythm(app):
    message = MessageWidget("assistant")
    message.set_text("A finished response.")
    message.set_streaming(False)
    app.processEvents()

    actions = message.message_actions
    assert actions is not None
    assert actions.isHidden() is False
    assert actions.height() == 30

    for button in (actions.copy_button, actions.dislike_button, actions.open_button):
        assert button.width() == 28
        assert button.height() == 28

    assert actions.time_label.height() == 28
    assert actions.time_label.alignment() & Qt.AlignmentFlag.AlignVCenter
    assert actions.time_label.alignment() & Qt.AlignmentFlag.AlignLeft
    message.close()


def test_copy_glyph_keeps_the_existing_action_kind(app):
    message = MessageWidget("assistant")
    actions = message.message_actions
    assert actions.copy_button.kind == "copy"
    assert actions.dislike_button.kind == "dislike"
    assert actions.open_button.kind == "open"
    message.close()


def test_feedback_selection_is_subtle_and_does_not_resize(app):
    message = MessageWidget("assistant")
    actions = message.message_actions
    before = actions.dislike_button.size()
    actions.dislike_button.setChecked(True)
    app.processEvents()
    assert actions.dislike_button.size() == before
    assert "#171b22" in actions.styleSheet()
    message.close()
