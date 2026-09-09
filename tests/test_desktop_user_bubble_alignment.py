from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.desktop import MessageWidget
from app.desktop import widgets as base


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_short_user_prose_is_optically_centered_without_changing_bubble_height_budget(app):
    message = MessageWidget("user")
    message.set_text("你好")
    message.show()
    app.processEvents()

    margins = message.layout().contentsMargins()
    # Keep the original 20 px total vertical inset, but compensate for Qt rich
    # text's invisible descent/leading so the visible glyphs sit in the middle.
    assert margins.top() == 18
    assert margins.bottom() == 2
    assert margins.top() + margins.bottom() == 20

    body = message._widgets[0]
    assert isinstance(body, base.RichLabel)
    assert body.alignment() & Qt.AlignmentFlag.AlignHCenter
    assert body.alignment() & Qt.AlignmentFlag.AlignVCenter
    message.close()


def test_code_or_mixed_user_content_keeps_symmetric_padding(app):
    message = MessageWidget("user")
    message.set_text("```python\nprint('hello')\n```")
    app.processEvents()

    margins = message.layout().contentsMargins()
    assert margins.top() == 10
    assert margins.bottom() == 10
    message.close()


def test_assistant_layout_is_not_touched(app):
    message = MessageWidget("assistant")
    before = message.layout().contentsMargins()
    message.set_text("Hello")
    after = message.layout().contentsMargins()

    assert (after.left(), after.top(), after.right(), after.bottom()) == (
        before.left(),
        before.top(),
        before.right(),
        before.bottom(),
    )
    message.close()
