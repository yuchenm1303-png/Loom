from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

# Importing the public desktop package installs the presentation pipeline,
# including the final low-reflow disclosure policy.
from app.desktop import TranscriptView
from app.desktop.message_presentation import ReasoningBlock
from app.desktop.state import TranscriptEntry


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_reasoning_disclosure_never_tweens_layout_height(app):
    block = ReasoningBlock()
    block.resize(720, 120)
    block.set_reasoning("Inspecting the current environment before answering.", live=False)

    assert block.body.isHidden()
    block._toggle()
    app.processEvents()

    assert block.body.isVisible()
    assert block.body.maximumHeight() == 16_777_215
    assert block.body.graphicsEffect() is None
    assert block._animation is None

    block._toggle()
    app.processEvents()

    assert block.body.isHidden()
    assert block.body.maximumHeight() == 16_777_215
    assert block._animation is None
    block.close()


def test_tool_disclosure_commits_geometry_once_and_suspends_tail_follow(app):
    view = TranscriptView()
    view.resize(980, 620)
    entry = TranscriptEntry(
        key="tool:exec:1",
        kind="tool",
        item={
            "type": "tool_call",
            "toolName": "exec",
            "status": "completed",
            "arguments": {"argv": ["python", "-m", "pytest", "-q"]},
            "content": "270 passed in 12.4s",
        },
    )
    view.render([entry])
    app.processEvents()

    card = view._widgets[entry.key]
    assert card.body_shell.isHidden()

    # Simulate the transcript following a live turn. A manual disclosure is
    # reading intent and must stop any scroll-to-tail animation before the card
    # changes geometry.
    view._follow_tail = True
    view._auto_scrolling = True
    card.toggle_button.click()
    app.processEvents()

    assert view._follow_tail is False
    assert view._auto_scrolling is False
    assert card.body_shell.isVisible()
    assert card.body_shell.maximumHeight() == 16_777_215
    assert card.body_shell.graphicsEffect() is None
    assert card._body_animation is None

    card.toggle_button.click()
    app.processEvents()
    assert card.body_shell.isHidden()
    assert card._body_animation is None
    view.close()
