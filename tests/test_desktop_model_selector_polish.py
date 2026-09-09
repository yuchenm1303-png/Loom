from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop.composer import ComposerPanel
from app.desktop.model_selector_polish import ModelSelectorButton


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_model_selector_is_iconless_compact_pill(app):
    panel = ComposerPanel()
    try:
        panel.set_model("MiniMax-M3")
        app.processEvents()

        button = panel.model_button
        assert isinstance(button, ModelSelectorButton)
        assert button.objectName() == "composerModel"
        assert button.icon().isNull()
        assert button.value == "MiniMax-M3"
        assert button.text() == "MiniMax-M3"
        assert button.minimumHeight() == 30
        assert button.maximumHeight() == 30
    finally:
        panel.close()
