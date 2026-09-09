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
    # A standalone block has no transcript viewport to snapshot, so it should
    # still take the atomic geometry path without creating a height tween.
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


def test_tool_disclosure_uses_snapshot_motion_without_height_tween(app):
    view = TranscriptView()
    view.resize(980, 620)
    view.show()
    entry = TranscriptEntry(
        key="tool:exec:1",
        kind="tool",
        item={
            "type": "tool_call",
            "toolName": "exec",
            "status": "completed",
            "arguments": {"argv": ["python", "-m", "pytest", "-q"]},
            "content": "270 passed in 12.4s\n" * 6,
        },
    )
    view.render([entry])
    app.processEvents()

    card = view._widgets[entry.key]
    assert card.body_shell.isHidden()

    # Manual disclosure is reading intent: freeze tail-follow before geometry
    # changes, commit the real card once, then animate only a cached viewport
    # slice. The live terminal/card itself must never carry a height animation or
    # an opacity effect.
    view._follow_tail = True
    view._auto_scrolling = True
    card.toggle_button.click()

    overlay = getattr(view, "_loom_disclosure_overlay", None)
    assert overlay is not None
    assert overlay.parentWidget() is view.viewport()
    assert overlay._animation is not None
    assert view._follow_tail is False
    assert view._auto_scrolling is False
    assert card.body_shell.isVisible()
    assert card.body_shell.maximumHeight() == 16_777_215
    assert card.body_shell.graphicsEffect() is None
    assert card._body_animation is None

    # Finish the visual overlay deterministically before exercising close.
    overlay.finish()
    app.processEvents()
    card.toggle_button.click()
    closing_overlay = getattr(view, "_loom_disclosure_overlay", None)
    assert closing_overlay is not None
    assert card.body_shell.isHidden()
    assert card._body_animation is None
    closing_overlay.finish()
    view.close()


def test_reasoning_in_transcript_uses_same_snapshot_motion(app):
    view = TranscriptView()
    view.resize(980, 620)
    view.show()
    entry = TranscriptEntry(
        key="assistant:1",
        kind="assistant",
        text="The environment check is complete.",
    )
    view.render([entry])
    app.processEvents()

    message = view._widgets[entry.key]
    reasoning = message.reasoning
    assert reasoning is not None
    reasoning.set_reasoning(
        "I checked the available tools, then verified the command before answering. " * 5,
        live=False,
    )
    app.processEvents()
    assert reasoning.body.isHidden()

    reasoning.toggle.click()
    overlay = getattr(view, "_loom_disclosure_overlay", None)
    assert overlay is not None
    assert overlay._animation is not None
    assert reasoning.body.isVisible()
    assert reasoning.body.maximumHeight() == 16_777_215
    assert reasoning.body.graphicsEffect() is None
    assert reasoning._animation is None

    overlay.finish()
    view.close()
