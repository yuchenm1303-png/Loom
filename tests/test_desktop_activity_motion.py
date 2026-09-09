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
    card.show()
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


def test_short_output_keeps_height_and_fade_motion(app, monkeypatch):
    """Short tool output commits geometry in one frame, just like every other body size.

    Earlier disclosure passes tweened ``maximumHeight`` and a small opacity fade
    for bodies under 180 px. The current low-reflow policy replaces that with a
    single layout commit plus the chevron's paint-only rotation; an opacity
    effect on the body surface is no longer set, so ``body_shell`` shows at
    its final height with no effect attached.
    """
    monkeypatch.setattr(theme, "motion_enabled", lambda: True)
    card = FlatActivityCard("tool")
    card.show()
    card.update_card(
        title="memory_status",
        status="completed",
        body="result\nok",
        auto_expand=False,
    )

    card._toggle()

    assert card.body_shell.isVisible()
    assert card._body_animation is None
    assert card.body.height() < 180
    assert card.body.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert card.body_shell.maximumHeight() == 16_777_215
    assert card.body_shell.graphicsEffect() is None


def test_exec_arguments_above_old_cutoff_still_animate(app, monkeypatch):
    """Typical multi-line exec JSON should commit one layout pass and not bounce.

    The earlier motion pass animated height/opacity for bodies under 180 px and
    tweened larger diffs with a parallel animation group. That looked responsive
    for short outputs but caused the whole transcript to reflow on every frame
    when a multi-line ``exec`` JSON crossed the 180 px cutoff. The current policy
    commits geometry once for every body size — the disclosure click flips the
    shell visible immediately and only the chevron rotates.
    """
    monkeypatch.setattr(theme, "motion_enabled", lambda: True)
    card = FlatActivityCard("tool")
    card.resize(720, 240)
    card.show()
    body = "\n".join(
        [
            '{',
            '  "argv": [',
            '    "powershell",',
            '    "-NoProfile",',
            '    "-ExecutionPolicy",',
            '    "Bypass",',
            '    "-Command",',
            '    "$items = Get-ChildItem -Force",',
            '    "$items | Select-Object Name,Length,LastWriteTime",',
            '    "$items | Sort-Object Length -Descending",',
            '    "$items | Format-Table -AutoSize"',
            '  ],',
            '  "timeout_seconds": 300,',
            '  "cwd": "C:/Users/example/Loom"',
            '}',
        ]
    )
    card.update_card(
        title="exec",
        status="completed",
        body=body,
        auto_expand=False,
    )

    card._toggle()

    assert card.body_shell.isVisible()
    assert card.body.height() > 180
    assert card.body_shell.maximumHeight() == 16_777_215
    assert card.body_shell.graphicsEffect() is None
    assert card._body_animation is None


def test_repeated_large_toggle_never_leaves_stale_animation(app, monkeypatch):
    monkeypatch.setattr(theme, "motion_enabled", lambda: True)
    card = FlatActivityCard("diff")
    card.show()
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
