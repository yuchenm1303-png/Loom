from __future__ import annotations

from app.agent_runtime.diff_tracker import TurnDiffTracker
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.workspace_tools import workspace_replace_tool, workspace_write_tool


def test_identical_replace_is_reported_as_no_change(tmp_path):
    target = tmp_path / "reference.md"
    target.write_text("### API\n", encoding="utf-8")
    tracker = TurnDiffTracker()
    context = ToolContext("session", "turn", tmp_path, services={"diff_tracker": tracker})

    result = workspace_replace_tool().handler(context, {
        "path": "reference.md",
        "old_text": "### API",
        "new_text": "### API",
    })

    assert result.ok is False
    assert "identical" in result.content
    assert target.read_text(encoding="utf-8") == "### API\n"
    assert tracker.revision == 0


def test_identical_write_does_not_create_a_diff_revision(tmp_path):
    target = tmp_path / "reference.md"
    target.write_text("existing content\n", encoding="utf-8")
    tracker = TurnDiffTracker()
    context = ToolContext("session", "turn", tmp_path, services={"diff_tracker": tracker})

    result = workspace_write_tool().handler(context, {
        "path": "reference.md",
        "text": "existing content\n",
    })

    assert result.ok is True
    assert result.data["changed"] is False
    assert tracker.revision == 0


def test_diff_tracker_ignores_unchanged_text():
    tracker = TurnDiffTracker()
    tracker.record_text_change("reference.md", before="same", after="same")
    assert tracker.revision == 0
    assert tracker.snapshot().paths == ()
