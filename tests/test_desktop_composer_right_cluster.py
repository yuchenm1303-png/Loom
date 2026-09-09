from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop.composer import ComposerPanel
from app.desktop.composer_polish import UsageBadge


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _panel(app: QApplication) -> ComposerPanel:
    panel = ComposerPanel()
    panel.resize(880, panel.sizeHint().height())
    panel.show()
    app.processEvents()
    return panel


def test_usage_is_metadata_not_a_fifth_action_button(app):
    panel = _panel(app)
    try:
        panel.set_usage(237_000)
        app.processEvents()

        assert isinstance(panel.usage_label, UsageBadge)
        assert panel.usage_label.text() == "237k tokens"
        assert panel.usage_label.height() >= 27
        assert "237,000 tokens" in panel.usage_label.toolTip()
    finally:
        panel.close()


def test_send_button_has_breathing_room_and_is_never_clipped(app):
    panel = _panel(app)
    try:
        send = panel.send_button
        outer = panel.layout()
        controls = outer.itemAt(1).layout()
        margins = controls.contentsMargins()

        assert send.width() == send.height() == 32
        assert margins.top() >= 4
        assert margins.bottom() >= 4

        # The complete styled button must fit inside the composer, not merely its
        # icon. This catches the fractional-DPI regression where the top edge of
        # the old 34px circle was cut by the controls row.
        assert send.geometry().top() >= 0
        assert send.geometry().bottom() < panel.height()
    finally:
        panel.close()
