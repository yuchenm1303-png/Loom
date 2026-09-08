from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

import app.desktop  # noqa: F401 - installs the desktop presentation hooks
from app.desktop.output_presentation import FlatActivityCard, TranscriptView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_main_transcript_uses_zero_gap_default_spacing(app):
    view = TranscriptView()
    assert view._layout.spacing() == 0


def test_activity_rows_use_single_line_chrome(app):
    card = FlatActivityCard("tool")
    margins = card.layout().contentsMargins()

    assert margins.top() == 0
    assert margins.bottom() == 0
    assert card.icon.size().width() == 16
    assert card.toggle_button.size().width() == 18


def test_activity_metadata_stays_out_of_collapsed_row(app):
    card = FlatActivityCard("tool")
    card.update_card(
        title="exec",
        subtitle='{ "argv": ["cmd", "/c", "echo hello"] }',
        status="running",
        body="",
    )

    assert card.subtitle_label.isHidden()
    assert "argv" in card.toolTip()


def test_explicit_runtime_spacing_is_preserved(app):
    view = TranscriptView(spacing=5, max_content_width=0)
    assert view._layout.spacing() == 5
