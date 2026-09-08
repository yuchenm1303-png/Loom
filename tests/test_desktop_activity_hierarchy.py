from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

import app.desktop  # noqa: F401 - installs presentation hooks
from app.desktop.output_presentation import FlatActivityCard, TranscriptView
from app.desktop.state import TranscriptEntry


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _tool(key: str, name: str, status: str = "completed") -> TranscriptEntry:
    return TranscriptEntry(
        key=key,
        kind="tool",
        item={"type": "tool_call", "toolName": name, "status": status},
    )


def _process(key: str, command: str = "cmd /c echo ok") -> TranscriptEntry:
    return TranscriptEntry(
        key=key,
        kind="process",
        item={
            "type": "process",
            "status": "completed",
            "argv": ["cmd", "/c", command.removeprefix("cmd /c ")],
            "stdout": "ok",
        },
    )


def _diff(key: str, path: str = r"C:\work\history.py") -> TranscriptEntry:
    return TranscriptEntry(
        key=key,
        kind="diff",
        item={
            "type": "file_edit",
            "status": "completed",
            "paths": [path],
            "diff": "+changed",
        },
    )


def test_main_transcript_hides_redundant_exec_request_when_process_exists(app):
    view = TranscriptView()
    view.render([_tool("tool:exec", "exec"), _process("process:1")])

    assert view.message_count() == 1
    assert view._order == ["process:1"]


def test_main_transcript_hides_redundant_write_request_when_diff_exists(app):
    view = TranscriptView()
    view.render([_tool("tool:write", "write_workspace_text"), _diff("diff:1")])

    assert view.message_count() == 1
    assert view._order == ["diff:1"]


def test_attention_tool_request_is_never_coalesced(app):
    view = TranscriptView()
    view.render([_tool("tool:exec", "exec", "waiting_approval"), _process("process:1")])

    assert view.message_count() == 2
    assert "tool:exec" in view._order


def test_activity_groups_get_semantic_headers(app):
    view = TranscriptView()
    entries = [_process("p1", "cmd /c echo one"), _process("p2", "cmd /c echo two"), _diff("d1")]
    view.render(entries)

    first = view._widgets["p1"]
    second = view._widgets["p2"]
    diff = view._widgets["d1"]

    assert isinstance(first, FlatActivityCard)
    assert first._activity_section_label.text() == "Commands"
    assert not first._activity_section.isHidden()
    assert second._activity_section.isHidden()
    assert diff._activity_section_label.text() == "Files"
    assert not diff._activity_section.isHidden()
    assert diff.title_label.text() == "Edited history.py"


def test_runtime_literal_view_keeps_raw_tool_rows(app):
    view = TranscriptView(max_content_width=0, spacing=5)
    view.render([_tool("tool:exec", "exec"), _process("process:1")])

    assert view.message_count() == 2
