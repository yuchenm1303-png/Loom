from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import app.desktop  # noqa: F401 - installs desktop presentation hooks
from app.desktop import theme
from app.desktop.output_presentation import FlatActivityCard


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_large_diff_reveal_settles_once_without_height_tween(app, monkeypatch):
    monkeypatch.setattr(theme, "motion_enabled", lambda: True)
    card = FlatActivityCard("diff")
    body = "\n".join(f"+ changed line {index}" for index in range(90))
    card.update_card(
        title="Edited disk_scan.ps1",
        status="completed",
        body=body,
        auto_expand=False,
    )

    assert card.body_shell.isHidden()
    card._toggle()

    assert card.body_shell.isVisible()
    assert card._body_animation is None
    assert card.body.height() == 360
    assert card.body.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    assert card.body_shell.maximumHeight() == 16_777_215
    assert card.body_shell.graphicsEffect() is None


def test_short_output_keeps_lightweight_height_motion(app, monkeypatch):
    monkeypatch.setattr(theme, "motion_enabled", lambda: True)
    card = FlatActivityCard("tool")
    card.update_card(
        title="memory_status",
        status="completed",
        body="result\nok",
        auto_expand=False,
    )

    card._toggle()

    assert card.body_shell.isVisible()
    assert card._body_animation is not None
    assert card.body.height() < 180
    assert card.body.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert card.body_shell.graphicsEffect() is None

    # Do not leave a live animation behind in the shared QApplication fixture.
    card._body_animation.stop()
    card._body_animation = None


def test_repeated_large_toggle_never_leaves_stale_animation(app, monkeypatch):
    monkeypatch.setattr(theme, "motion_enabled", lambda: True)
    card = FlatActivityCard("diff")
    card.update_card(
        title="Edited runtime.py",
        status="completed",
        body="\n".join(f"+ line {index}" for index in range(80)),
        auto_expand=False,
    )

    card._toggle()
    assert card.body_shell.isVisible()
    assert card._body_animation is None

    card._toggle()
    assert card.body_shell.isHidden()
    assert card._body_animation is None

    card._toggle()
    assert card.body_shell.isVisible()
    assert card._body_animation is None
