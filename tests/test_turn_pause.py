from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from app.agent_runtime.runtime import CancellationToken
from app.agent_runtime import AgentTool, ToolEffect, ToolResult
from app.ai import ModelResponse, ToolCall
from test_app_server import _build_service, _wait_until


@pytest.mark.parametrize("cancel", [False, True])
def test_pause_gate_resumes_or_cancels_without_deadlock(cancel):
    token = CancellationToken()
    token.set_paused(True)
    with ThreadPoolExecutor() as pool:
        waiting = pool.submit(token.wait_if_paused)
        time.sleep(0.03)
        assert not waiting.done()
        if cancel:
            token.cancel()
        else:
            token.set_paused(False)  # replaced below
        waiting.result(timeout=1)
        assert token.cancelled is cancel


@pytest.mark.parametrize("stop", [False, True])
def test_pause_preserves_tool_result_and_same_turn(tmp_path, stop):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def inspect(_context, _arguments):
        calls.append("inspect")
        entered.set()
        assert release.wait(3)
        return ToolResult(ok=True, content="saved inspection result")

    tool = AgentTool(name="inspect", description="Inspect once", input_schema={"type": "object", "properties": {}}, handler=inspect, effect=ToolEffect.READ_ONLY)
    service, runtime, store, platform, workspace = _build_service(tmp_path, [
        ModelResponse(tool_calls=(ToolCall(call_id="one", name="inspect", arguments={}),), finish_reason="tool_calls"),
        ModelResponse(text="Finished", finish_reason="stop"),
    ], tools=(tool,))
    try:
        thread_id = service.thread_start({"workspaceDir": str(workspace)})["thread"]["id"]
        turn_id = service.turn_start({"threadId": thread_id, "input": "inspect"})["turn"]["id"]
        params = {"threadId": thread_id, "turnId": turn_id}
        assert entered.wait(2)
        with pytest.raises(ValueError, match="turnId"):
            service.turn_pause({**params, "turnId": "wrong"})
        assert not runtime.is_paused(thread_id)
        assert service.turn_pause(params)["accepted"]
        assert service.thread_read({"threadId": thread_id})["thread"]["paused"]
        release.set()
        time.sleep(0.1)
        assert len(platform.requests) == 1
        assert service._is_active(thread_id)
        if stop:
            service.turn_interrupt(params)
        else:
            assert service.turn_resume(params)["accepted"]
        _wait_until(lambda: not service._is_active(thread_id))
        snapshot = service.thread_read({"threadId": thread_id})
        assert calls == ["inspect"]
        assert not snapshot["thread"]["paused"]
        if not stop:
            assert len(platform.requests) == 2
            assert "saved inspection result" in str(platform.requests[1].messages)
            assert snapshot["finalText"] == "Finished"
            assert snapshot["turns"][0]["id"] == turn_id
    finally:
        release.set()
        runtime.cancel(thread_id)
        runtime.close()
