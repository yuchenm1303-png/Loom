from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop.state import TranscriptEntry
from app.desktop import TranscriptView


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
