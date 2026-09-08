from __future__ import annotations

import os
import pytest
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QPushButton, QSplitter, QTabWidget, QWidget
from app.desktop import theme
from app.desktop.widgets import EmptyState
from app.desktop.interaction import SuggestionCard
from app.desktop.composer import ComposerPanel
from app.desktop.sidebar_motion import SidebarMotionController


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_starter_keyboard_action_and_small_window_fit(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    view = EmptyState()
    view.setStyleSheet(theme.stylesheet())
    view.resize(520, 540)
    chosen = []
    view.promptChosen.connect(chosen.append)
    view.show()
    app.processEvents()
    cards = view.findChildren(SuggestionCard)
    assert len(cards) == 4
    for index, card in enumerate(cards):
        card.setFocus()
        QTest.keyClick(card, Qt.Key.Key_Space)
        assert chosen[-1] == view.PROMPTS[index][1]
        assert card.height() >= 90
        assert card.geometry().right() < card.parentWidget().width()
    assert abs(cards[0].width() - cards[1].width()) <= 1
    view.close()


def test_focus_feedback_preserves_editor_content(app, monkeypatch):
    monkeypatch.delenv("LOOM_REDUCE_MOTION", raising=False)
    composer = ComposerPanel()
    composer.resize(650, 160)
    composer.show()
    composer.editor.setPlainText("Keep the draft intact")
    for focused in (True, False, True, False):
        composer.animate_focus(focused)
        QTest.qWait(20)
    QTest.qWait(220)
    assert composer.editor.toPlainText() == "Keep the draft intact"
    assert composer.graphicsEffect() is None
    assert composer._focus_progress == 0.0
    composer.close()


def test_rapid_panel_reversal_and_tab_switch_cleanup(app, monkeypatch):
    monkeypatch.delenv("LOOM_REDUCE_MOTION", raising=False)
    window = QWidget()
    window._sidebar_visible = window._runtime_visible = True
    layout = QHBoxLayout(window)
    window.main_splitter = QSplitter()
    layout.addWidget(window.main_splitter)
    window.sidebar_panel = QFrame()
    window.activity_panel = QFrame()
    for panel in (window.sidebar_panel, window.activity_panel):
        panel.setMinimumWidth(120)
        panel.setMaximumWidth(300)
    for panel in (window.sidebar_panel, QWidget(), window.activity_panel):
        window.main_splitter.addWidget(panel)
    window.sidebar_toggle_button = QPushButton(window)
    window.runtime_toggle_button = QPushButton(window)
    window.activity_tabs = QTabWidget(window.activity_panel)
    for title in ("Activity", "Terminal", "Diff"):
        window.activity_tabs.addTab(QWidget(), title)
    controller = SidebarMotionController(window)
    window.resize(950, 600)
    window.show()
    app.processEvents()
    for index in (1, 2, 0, 2):
        window.activity_tabs.setCurrentIndex(index)
        QTest.qWait(20)
    for visible in (False, True, False, True):
        controller.set_panel_visible("sidebar", window.sidebar_panel, visible)
        QTest.qWait(30)
    QTest.qWait(350)
    assert window.sidebar_panel.isVisible()
    assert 120 <= window.sidebar_panel.width() <= 300
    assert window.sidebar_panel.minimumWidth() == 120
    assert window.sidebar_panel.maximumWidth() == 300
    assert not controller._panel_animations
    assert all(window.activity_tabs.widget(i).graphicsEffect() is None for i in range(3))
    window.close()
