from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QFrame, QPushButton, QTabWidget, QWidget

from app.desktop import widgets
from app.desktop.sidebar_motion import SidebarMotionController


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class DummyWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._sidebar_visible = True
        self._runtime_visible = True

        self.sidebar_panel = QFrame(self)
        self.sidebar_panel.setMinimumWidth(120)
        self.sidebar_panel.setMaximumWidth(320)
        self.sidebar_panel.resize(220, 300)

        self.activity_panel = QFrame(self)
        self.activity_panel.setMinimumWidth(160)
        self.activity_panel.setMaximumWidth(420)
        self.activity_panel.resize(260, 300)

        self.sidebar_toggle_button = QPushButton(self)
        self.runtime_toggle_button = QPushButton(self)

        self.activity_tabs = QTabWidget(self)
        for label in ("Activity", "Terminal", "Diff"):
            self.activity_tabs.addTab(QWidget(), label)
        self.activity_tabs.resize(360, 240)


def test_reduced_motion_panel_toggle_is_immediate_and_preserves_constraints(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    window = DummyWindow()
    controller = SidebarMotionController(window)

    minimum = window.activity_panel.minimumWidth()
    maximum = window.activity_panel.maximumWidth()

    controller.set_panel_visible("runtime", window.activity_panel, False)
    assert window.activity_panel.isHidden()
    assert window.activity_panel.minimumWidth() == minimum
    assert window.activity_panel.maximumWidth() == maximum

    controller.set_panel_visible("runtime", window.activity_panel, True)
    assert not window.activity_panel.isHidden()
    assert window.activity_panel.minimumWidth() == minimum
    assert window.activity_panel.maximumWidth() == maximum


def test_runtime_tab_indicator_tracks_current_tab(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    window = DummyWindow()
    controller = SidebarMotionController(window)
    window.show()
    app.processEvents()

    window.activity_tabs.setCurrentIndex(1)
    app.processEvents()
    controller._snap_indicator()

    target = window.activity_tabs.tabBar().tabRect(1)
    indicator = controller._indicator.geometry()
    assert indicator.width() > 0
    assert target.left() <= indicator.center().x() <= target.right()

    window.close()


def test_new_live_runtime_event_gets_reveal_and_icon_pulse(app, monkeypatch):
    monkeypatch.delenv("LOOM_REDUCE_MOTION", raising=False)
    view = widgets.ActivityTimelineView()
    view.render_events([("15:00:00", "session_created", "Session started")])
    view.render_events(
        [
            ("15:00:00", "session_created", "Session started"),
            ("15:00:01", "tool_started", "Running computer_status"),
        ]
    )

    item = view._layout.itemAt(1)
    row = item.widget() if item is not None else None
    assert row is not None
    assert getattr(row, "_loom_row_reveal", None) is not None

    icon = row.findChild(widgets.VectorIcon)
    assert icon is not None
    pulse = getattr(icon, "_loom_live_pulse", None)
    assert pulse is not None
    assert pulse.loopCount() == -1
    pulse.stop()
