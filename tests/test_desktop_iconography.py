from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop import widgets


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_runtime_tab_icons_keep_transparent_corners(app):
    """Native tab icons must not inherit the QWidget panel background."""
    icon = widgets.vector_icon("terminal", size=18)
    image = icon.pixmap(18, 18).toImage()

    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(17, 17).alpha() == 0


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("session_created", ("session", "muted")),
        ("user_message", ("prompt", "muted")),
        ("turn_started", ("turn_start", "muted")),
        ("model_requested", ("model_request", "accent")),
        ("model_response", ("model_response", "accent")),
        ("tool_requested", ("tool", "tool")),
        ("tool_started", ("spinner", "tool")),
        ("tool_completed", ("check", "good")),
        ("tool_approval_required", ("approval", "warn")),
        ("turn_completed", ("check_check", "good")),
        ("turn_failed", ("error", "bad")),
        ("process_started", ("terminal", "tool")),
        ("process_exited", ("terminal_done", "good")),
    ],
)
def test_activity_event_icons_encode_event_semantics(kind, expected):
    assert widgets._event_icon(kind) == expected
