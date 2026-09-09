from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QListWidget

from app.desktop.thread_presentation import StatusBeacon, ThreadListItemWidget, thread_row_size


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _record(**overrides):
    record = {
        "id": "thread-1",
        "title": "进度怎么样了",
        "status": "completed",
        "updatedAt": "2026-09-09T08:00:00Z",
        "workspace": "C:/work/Loom",
    }
    record.update(overrides)
    return record


def test_thread_row_is_compact_and_list_owns_no_second_selection_surface(qt_app):
    view = QListWidget()
    view.setObjectName("threadList")
    row = ThreadListItemWidget(_record(), view)

    assert thread_row_size(row).height() == 38
    assert "QScrollBar::handle:vertical" in view.styleSheet()
    assert "item:selected" in view.styleSheet()
    assert row.meta_label.isHidden()
    assert row.status_dot.isHidden()


def test_active_row_uses_soft_surface_and_short_accent_rail(qt_app):
    view = QListWidget()
    view.setObjectName("threadList")
    row = ThreadListItemWidget(_record(id="thread-active"), view)
    row.resize(280, 38)

    row.set_active(True)

    assert row.title_label.property("active") is True
    assert row._surface_effect.opacity() == pytest.approx(1.0)
    assert row._marker_effect.opacity() == pytest.approx(0.96)
    assert row.marker.height() == 20


def test_attention_state_uses_native_beacon_instead_of_font_bullet(qt_app):
    view = QListWidget()
    view.setObjectName("threadList")
    row = ThreadListItemWidget(_record(id="thread-running", status="running"), view)

    assert isinstance(row.status_dot, StatusBeacon)
    assert row.status_dot.text() == "●"
    assert row.status_dot.property("state") == "running"
    assert row.status_dot.size().width() == 14
    assert row.status_dot.isHidden() is False
