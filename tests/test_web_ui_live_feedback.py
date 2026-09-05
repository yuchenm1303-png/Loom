from __future__ import annotations

from pathlib import Path


def _app_js() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "app" / "web_static" / "app.js").read_text(encoding="utf-8")


def test_web_ui_shows_immediate_live_working_feedback() -> None:
    js = _app_js()

    assert "function showOptimisticWorkingState()" in js
    assert "function createWorkingNode(text)" in js
    assert "Waiting for ${state.bootstrap?.model || \"model\"}…" in js
    assert "data-loom-working='true'" in js
    assert "state.workingSince = Date.now();" in js
    assert "workingElapsedSeconds" in js


def test_web_ui_live_status_is_grounded_in_runtime_events() -> None:
    js = _app_js()

    assert "function latestLiveEvent(snapshot)" in js
    for kind in (
        "model_requested",
        "tool_requested",
        "tool_started",
        "tool_completed",
        "tool_failed",
        "process_started",
        "turn_diff_updated",
    ):
        assert f'\"{kind}\"' in js
    assert 'data.nested ? "Code Mode · " : ""' in js
    assert "Running ${tool}…" in js
    assert "finished · continuing…" in js


def test_web_ui_uses_adaptive_polling_without_claiming_token_streaming() -> None:
    js = _app_js()

    assert "const ACTIVE_POLL_MS = 450;" in js
    assert "const IDLE_POLL_MS = 1100;" in js
    assert "function schedulePoll(delay)" in js
    assert "async function pollLoop()" in js
    assert "setInterval" not in js
    assert "token streaming" not in js.casefold()
