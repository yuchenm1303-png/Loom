from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from app.desktop import TranscriptView
from app.desktop.state import TranscriptEntry
from app.desktop.transcript_disclosure import AnimatedReveal, FlowActivityCard, ReasoningDisclosure


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _settle(app: QApplication, *, seconds: float = 0.26) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def test_reveal_progress_physically_moves_following_layout_content(app):
    root = QWidget()
    root.resize(480, 320)
    layout = QVBoxLayout(root)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    content = QLabel("detail\n" * 8)
    reveal = AnimatedReveal(content, root)
    following = QLabel("following row", root)
    layout.addWidget(reveal)
    layout.addWidget(following)
    layout.addStretch(1)
    root.show()
    app.processEvents()

    reveal.refresh_target()
    collapsed_y = following.y()

    reveal._expanded = True
    reveal._set_progress(0.5)
    layout.invalidate()
    layout.activate()
    app.processEvents()
    middle_y = following.y()

    reveal._set_progress(1.0)
    layout.invalidate()
    layout.activate()
    app.processEvents()
    expanded_y = following.y()

    assert reveal.target_height > 0
    assert collapsed_y < middle_y < expanded_y
    assert middle_y - collapsed_y == pytest.approx(
        (expanded_y - collapsed_y) / 2,
        abs=2,
    )
    root.close()


def test_reveal_progress_writes_geometry_only_when_pixel_height_changes(app):
    class CountingReveal(AnimatedReveal):
        def __init__(self, content, parent=None):
            self.minimum_writes = 0
            self.maximum_writes = 0
            super().__init__(content, parent)

        def setMinimumHeight(self, height):  # noqa: N802 - Qt API
            self.minimum_writes += 1
            super().setMinimumHeight(height)

        def setMaximumHeight(self, height):  # noqa: N802 - Qt API
            self.maximum_writes += 1
            super().setMaximumHeight(height)

    reveal = CountingReveal(QLabel("detail"))
    # Ignore the one-time construction constraints. The animation path should
    # never mutate minimumHeight, and repeated progress samples that round to the
    # same pixel must not invalidate layout again.
    reveal.minimum_writes = 0
    reveal.maximum_writes = 0
    reveal._target_height = 100
    reveal._geometry_height = 0

    reveal._set_progress(0.251)
    assert reveal.minimumHeight() == 0
    assert reveal.maximumHeight() == 25
    assert reveal.minimum_writes == 0
    assert reveal.maximum_writes == 1

    reveal._set_progress(0.252)
    assert reveal.maximumHeight() == 25
    assert reveal.minimum_writes == 0
    assert reveal.maximum_writes == 1

    reveal._set_progress(0.26)
    assert reveal.maximumHeight() == 26
    assert reveal.maximum_writes == 2
    reveal.close()


def test_task_disclosure_uses_one_real_reveal_without_viewport_overlay(app, monkeypatch):
    monkeypatch.delenv("LOOM_REDUCE_MOTION", raising=False)
    view = TranscriptView()
    view.resize(980, 620)
    view.show()
    entries = [
        TranscriptEntry(
            key="tool:exec:1",
            kind="tool",
            item={
                "type": "tool_call",
                "toolName": "exec",
                "status": "completed",
                "arguments": {"argv": ["python", "-m", "pytest", "-q"]},
                "content": "270 passed in 12.4s\n" * 6,
            },
        ),
        TranscriptEntry(
            key="tool:next:1",
            kind="tool",
            item={
                "type": "tool_call",
                "toolName": "read_file",
                "status": "completed",
                "arguments": {"path": "README.md"},
                "content": "done",
            },
        ),
    ]
    view.render(entries)
    app.processEvents()

    card = view._widgets[entries[0].key]
    following = view._widgets[entries[1].key]
    assert isinstance(card, FlowActivityCard)
    assert isinstance(card.reveal, AnimatedReveal)
    assert not hasattr(view, "_loom_disclosure_overlay")

    start_y = following.y()
    card.toggle_button.click()
    assert card.reveal._animation is not None
    assert card._body_animation is None
    assert card.toggle_button.progress == pytest.approx(card.reveal.progress)

    _settle(app)
    assert card.reveal.progress == pytest.approx(1.0)
    assert card.toggle_button.progress == pytest.approx(1.0)
    assert following.y() > start_y
    assert not hasattr(view, "_loom_disclosure_overlay")

    card.toggle_button.click()
    _settle(app)
    assert card.reveal.progress == pytest.approx(0.0)
    assert card.toggle_button.progress == pytest.approx(0.0)
    view.close()


def test_reasoning_uses_the_same_disclosure_component(app, monkeypatch):
    monkeypatch.delenv("LOOM_REDUCE_MOTION", raising=False)
    view = TranscriptView()
    view.resize(980, 620)
    view.show()
    entry = TranscriptEntry(
        key="assistant:1",
        kind="assistant",
        text="<think>I inspected the environment before answering.</think>Done.",
    )
    view.render([entry])
    app.processEvents()

    message = view._widgets[entry.key]
    reasoning = message.reasoning
    assert isinstance(reasoning, ReasoningDisclosure)
    assert isinstance(reasoning.reveal, AnimatedReveal)
    assert reasoning.reveal.progress == 0.0

    reasoning.toggle.click()
    assert reasoning.reveal._animation is not None
    _settle(app)
    assert reasoning.reveal.progress == pytest.approx(1.0)
    assert reasoning.toggle.progress == pytest.approx(1.0)

    reasoning.toggle.click()
    _settle(app)
    assert reasoning.reveal.progress == pytest.approx(0.0)
    assert reasoning.toggle.progress == pytest.approx(0.0)
    view.close()
