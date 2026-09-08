from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

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


def test_usage_chip_and_send_button_polish(app):
    panel = ComposerPanel()
    panel.set_usage(64_679)

    assert panel.usage_label.objectName() == "composerUsage"
    assert panel.usage_label.text() == "64.7k tokens"
    assert panel.usage_label.toolTip() == "64,679 tokens in this conversation"
    assert panel.send_button.text() == ""
    assert not panel.send_button.icon().isNull()
    assert panel.send_button.width() == 32
    assert panel.send_button.height() == 32
