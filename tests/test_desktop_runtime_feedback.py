from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop import turn_progress
from app.desktop.output_presentation import FlatActivityCard
from app.desktop.runtime_feedback import _coalesce_activity, describe_action
from app.desktop.state import TranscriptEntry


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_running_process_is_visibly_live_and_opens_streaming_output(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    card = FlatActivityCard("process")
    card.update_card(
        title="$ powershell.exe -NoProfile -File C:\\tmp\\c_disk_inspect2.ps1",
        status="running",
        body="$ powershell.exe -NoProfile -File C:\\tmp\\c_disk_inspect2.ps1\nchecking C:...",
    )

    assert card.property("runtimeState") == "running"
    assert card.runtime_badge.isHidden() is False
    assert card.runtime_badge.label.text().startswith("Running ·")
    assert card._expanded is True
    assert card.body_shell.isHidden() is False
    assert "checking C:" in card.body.toPlainText()
    card.close()


def test_live_process_keeps_its_output_open_when_it_finishes(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    card = FlatActivityCard("process")
    card.update_card(title="$ python job.py", status="running", body="$ python job.py\nstep 1")
    assert card._expanded is True

    card.update_card(
        title="$ python job.py",
        status="completed",
        body="$ python job.py\nstep 1\ndone",
    )

    assert card.property("runtimeState") == ""
    assert card.runtime_badge.isHidden() is True
    assert card._expanded is True
    assert card.body_shell.isHidden() is False
    assert "done" in card.body.toPlainText()
    card.close()


def test_historical_completed_process_stays_compact(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    card = FlatActivityCard("process")
    card.update_card(
        title="$ python old_job.py",
        status="completed",
        body="$ python old_job.py\nfinished yesterday",
    )

    assert card.runtime_badge.isHidden() is True
    assert card._expanded is False
    assert card.body_shell.isHidden() is True
    card.close()


def test_duplicate_live_exec_tool_is_hidden_once_process_row_exists():
    entries = [
        TranscriptEntry(
            key="tool:1",
            kind="tool",
            item={"type": "tool_call", "toolName": "exec", "status": "running"},
        ),
        TranscriptEntry(
            key="process:1",
            kind="process",
            item={"type": "process", "status": "running", "command": "python job.py"},
        ),
    ]

    visible = _coalesce_activity(entries)
    assert [entry.kind for entry in visible] == ["process"]


def test_current_process_action_is_compact_and_human_readable():
    label = describe_action(
        {
            "item": {
                "type": "process",
                "argv": [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    "C:\\Users\\Jack\\AppData\\Local\\Temp\\c_disk_inspect2.ps1",
                ],
            }
        }
    )
    assert label == "Running c_disk_inspect2.ps1"


def test_turn_progress_surface_names_the_current_action(app, monkeypatch):
    monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    line = turn_progress.TurnProgressLine()
    assert line.minimumHeight() >= 38
    assert "background:#11151d" in line.styleSheet()
    assert line.rule.isHidden() is True

    class Window:
        _turn_phase = "working"
        _turn_progress_action = "Running c_disk_inspect2.ps1"
        _turn_progress_started_at = time.monotonic() - 3.2

    text = turn_progress._phase_text(Window())
    assert text.startswith("Running c_disk_inspect2.ps1 · ")
    assert text.endswith("3s")
    line.close()
