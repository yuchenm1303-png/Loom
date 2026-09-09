from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop import theme
from app.desktop.composer import ComposerPanel
from app.desktop.composer_polish import _compact_tokens


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (999, "999"),
        (1_000, "1.0k"),
        (64_679, "64.7k"),
        (647_900, "648k"),
        (1_250_000, "1.2m"),
    ],
)
def test_compact_token_format(value, expected):
    assert _compact_tokens(value) == expected


def test_composer_controls_use_semantic_polish_and_vector_icons(app):
    panel = ComposerPanel()
    panel.set_workspace("C:/work/Loom")
    panel.set_permission("full-access")
    panel.set_model("MiniMax-M3")
    panel.set_usage(64_679)

    assert panel.workspace_button.objectName() == "composerWorkspace"
    assert panel.permission_button.objectName() == "composerPermission"
    assert panel.model_button.objectName() == "composerModel"
    assert panel.workspace_button.text() == "Loom"
    assert panel.permission_button.text() == "full-access"
    assert panel.model_button.text() == "MiniMax-M3"
    assert panel.permission_button.property("mode") == "full-access"

    for button in (
        panel.workspace_button,
        panel.permission_button,
        panel.model_button,
    ):
        assert not button.icon().isNull()
        assert button.minimumHeight() == 29
        assert button.maximumHeight() == 29

    assert panel.usage_label.objectName() == "composerUsage"
    assert panel.usage_label.text() == "64.7k tokens"
    assert panel.usage_label.toolTip() == "64,679 tokens in this conversation"

    assert panel.send_button.text() == ""
    assert not panel.send_button.icon().isNull()
    assert panel.send_button.width() == 34
    assert panel.send_button.height() == 34

    panel.close()


def test_composer_polish_styles_all_control_states():
    qss = theme.stylesheet()
    assert "QPushButton#composerWorkspace" in qss
    assert "QPushButton#composerPermission" in qss
    assert "QPushButton#composerModel" in qss
    assert 'QPushButton#composerPermission[mode="full-access"]' in qss
    assert "QLabel#composerUsage" in qss
    assert "QPushButton#sendButton" in qss
    assert "QPushButton#stopButton" in qss
