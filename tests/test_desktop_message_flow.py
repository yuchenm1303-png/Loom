from __future__ import annotations

from datetime import datetime, timezone

from app.desktop_message_flow import build_message_flow, elapsed_label, item_from_record


def test_elapsed_labels_match_turn_state():
    assert elapsed_label(
        "2026-09-07T12:00:00+00:00",
        "2026-09-07T12:03:43+00:00",
        "completed",
    ) == "Worked for 3m 43s"
    assert elapsed_label(
        "2026-09-07T12:00:00+00:00",
        None,
        "running",
        now=datetime(2026, 9, 7, 12, 0, 38, tzinfo=timezone.utc),
    ) == "Working · 38s"


def test_flow_interleaves_real_turn_items_and_deduplicates_exec_wrapper():
    snapshot = {
        "thread": {"currentTurnId": "turn-1"},
        "turns": [
            {
                "id": "turn-1",
                "status": "completed",
                "startedAt": "2026-09-07T12:00:00+00:00",
                "completedAt": "2026-09-07T12:00:12+00:00",
                "source": "user",
                "items": [
                    {
                        "id": "user:1",
                        "turnId": "turn-1",
                        "type": "user_message",
                        "status": "completed",
                        "createdAt": "2026-09-07T12:00:00+00:00",
                        "text": "Inspect this project",
                    },
                    {
                        "id": "tool:exec-1",
                        "turnId": "turn-1",
                        "type": "tool_call",
                        "status": "completed",
                        "createdAt": "2026-09-07T12:00:02+00:00",
                        "toolName": "exec",
                        "arguments": {"argv": ["python", "-V"]},
                        "ok": True,
                    },
                    {
                        "id": "process:proc-1",
                        "turnId": "turn-1",
                        "type": "process",
                        "status": "completed",
                        "createdAt": "2026-09-07T12:00:03+00:00",
                        "processId": "proc-1",
                        "argv": ["python", "-V"],
                        "cwd": "C:/repo",
                        "stdout": "Python 3.12.10\n",
                        "returncode": 0,
                        "sandbox": {"enforced": True, "backend": "windows-mxc"},
                    },
                    {
                        "id": "diff:1",
                        "turnId": "turn-1",
                        "type": "file_edit",
                        "status": "completed",
                        "createdAt": "2026-09-07T12:00:07+00:00",
                        "paths": ["app/demo.py"],
                        "diff": "+hello\n",
                    },
                    {
                        "id": "assistant:1",
                        "turnId": "turn-1",
                        "type": "assistant_message",
                        "status": "completed",
                        "createdAt": "2026-09-07T12:00:11+00:00",
                        "text": "Done.",
                    },
                ],
            }
        ],
        "events": [],
        "messages": [],
    }

    flow = build_message_flow(snapshot)

    assert len(flow) == 1
    assert flow[0].elapsed(now=datetime(2026, 9, 7, 12, 0, 12, tzinfo=timezone.utc)) == "Worked for 12s"
    assert [item.kind for item in flow[0].items] == ["user", "process", "file", "assistant"]
    process = next(item for item in flow[0].items if item.kind == "process")
    assert process.title == "Ran command"
    assert process.detail == "$ python -V"
    assert "Python 3.12.10" in process.body
    assert "windows-mxc" in process.body
    changed = next(item for item in flow[0].items if item.kind == "file")
    assert changed.title == "Changed 1 file"
    assert changed.detail == "app/demo.py"


def test_context_compaction_becomes_standalone_flow_item():
    snapshot = {
        "thread": {},
        "turns": [],
        "messages": [],
        "events": [
            {
                "eventId": "evt-compact",
                "turnId": None,
                "kind": "context_checkpointed",
                "createdAt": "2026-09-07T12:04:00+00:00",
                "data": {
                    "summary_source": "model",
                    "archived_messages": 72,
                    "retained_messages": 24,
                    "checkpoint_id": "checkpoint-1",
                },
            }
        ],
    }

    flow = build_message_flow(snapshot)

    assert len(flow) == 1
    assert flow[0].source == "system"
    assert flow[0].items[0].kind == "context"
    assert flow[0].items[0].title == "Context automatically compacted"
    assert flow[0].items[0].detail == "archived 72 · retained 24"


def test_legacy_message_snapshot_remains_visible_around_runtime_items():
    snapshot = {
        "thread": {"currentTurnId": "turn-1"},
        "turns": [
            {
                "id": "turn-1",
                "status": "completed",
                "startedAt": "2026-09-07T12:00:00+00:00",
                "completedAt": "2026-09-07T12:00:05+00:00",
                "items": [
                    {
                        "id": "process:p1",
                        "turnId": "turn-1",
                        "type": "process",
                        "status": "completed",
                        "processId": "p1",
                        "argv": ["git", "status"],
                    }
                ],
            }
        ],
        "messages": [
            {"role": "user", "content": "Check status"},
            {"role": "assistant", "content": "Working tree is clean."},
        ],
        "events": [],
    }

    flow = build_message_flow(snapshot)
    kinds = [item.kind for item in flow[0].items]

    assert kinds[0] == "user"
    assert "process" in kinds
    assert kinds[-1] == "assistant"
    assert flow[0].items[0].text == "Check status"
    assert flow[0].items[-1].text == "Working tree is clean."


def test_live_runtime_item_is_merged_without_waiting_for_snapshot_reconcile():
    snapshot = {
        "thread": {"currentTurnId": "turn-live"},
        "turns": [],
        "messages": [],
        "events": [],
    }
    live_turns = {
        "turn-live": {
            "id": "turn-live",
            "status": "running",
            "startedAt": "2026-09-07T12:00:00+00:00",
        }
    }
    live_items = [
        {
            "id": "process:live",
            "turnId": "turn-live",
            "type": "process",
            "status": "running",
            "createdAt": "2026-09-07T12:00:02+00:00",
            "processId": "live",
            "argv": ["pytest", "-q"],
        }
    ]

    flow = build_message_flow(snapshot, live_turns=live_turns, live_items=live_items)

    assert len(flow) == 1
    assert flow[0].status == "running"
    assert flow[0].items[0].title == "Running command"
    assert flow[0].items[0].detail == "$ pytest -q"


def test_tool_summaries_handle_lists_and_structured_results_without_hashing_errors():
    item = item_from_record(
        {
            "id": "tool:b1",
            "turnId": "turn-1",
            "type": "tool_call",
            "status": "completed",
            "toolName": "browser_navigate",
            "arguments": {"url": "https://example.com", "selector": []},
            "result": {"title": "Example"},
        }
    )

    assert item is not None
    assert item.kind == "browser"
    assert item.detail == "https://example.com"
    assert "Example" in item.body
