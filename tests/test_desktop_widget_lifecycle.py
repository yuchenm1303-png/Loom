from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop.state import TranscriptEntry
from app.desktop import TranscriptView
from app.desktop.widgets import ActivityTimelineView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_retired_transcript_widget_never_becomes_a_top_level_window(app):
    view = TranscriptView()
    entry = TranscriptEntry(key="assistant:1", kind="assistant", text="hello")
    view.render([entry])
    retired = view._widgets[entry.key]

    view.render([])

    # deleteLater is intentionally deferred, but the widget must stay parented
    # and hidden during that interval. setParent(None) on a visible QWidget can
    # otherwise create the blank native window titled "Loom" seen on Windows.
    assert retired.parentWidget() is view.canvas
    assert retired.isHidden()
    assert not retired.isWindow()
    view.close()


def test_retired_stream_block_stays_owned_until_deferred_delete(app):
    view = TranscriptView()
    first = TranscriptEntry(
        key="assistant:1",
        kind="assistant",
        text="first paragraph\n\n```text\nold\n```",
    )
    view.render([first])
    message = view._widgets[first.key]
    retired_block = message._widgets[-1]

    # Changing the tail block kind forces the old block down the disposal path.
    message.set_text("first paragraph\n\nreplacement prose")

    assert retired_block.parentWidget() is message
    assert retired_block.isHidden()
    assert not retired_block.isWindow()
    view.close()


def test_retired_runtime_activity_row_never_becomes_a_top_level_window(app):
    """Model-step refreshes must not flash Activity rows as native Loom windows."""

    view = ActivityTimelineView()
    view.resize(360, 420)
    view.show()
    view.render_events([("15:03:18", "model_started", "Asked model · step 6")])
    app.processEvents()

    item = view._layout.itemAt(0)
    retired = item.widget()
    assert retired is not None
    assert retired.parentWidget() is view.canvas

    # render_events rebuilds the timeline on each notification. The historical
    # setParent(None) here promoted this exact ActivityEventRow to the floating
    # white/black window shown by the user before deleteLater() ran.
    view.render_events([("15:03:19", "model_completed", "Model replied · step 6")])

    assert retired.parentWidget() is view.canvas
    assert retired.isHidden()
    assert not retired.isWindow()
    view.close()


def test_retired_runtime_empty_panel_stays_owned_until_delete(app):
    view = ActivityTimelineView()
    view.resize(360, 420)
    view.show()
    view.render_events([])
    app.processEvents()

    empty = view._layout.itemAt(0).widget()
    assert empty is not None

    view.render_events([("15:03:18", "model_started", "Asked model · step 6")])

    assert empty.parentWidget() is view.canvas
    assert empty.isHidden()
    assert not empty.isWindow()
    view.close()
