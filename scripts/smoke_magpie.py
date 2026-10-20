"""Opt-in live gateway test; uses account quota and an isolated temporary workspace."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loom_cli import _build_runtime, _create_workspace_session, build_parser
from app.agent_runtime import AgentEventKind, AgentStatus, PermissionMode
from app.agent_runtime.contracts import AgentLimits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3425/v1")
    parser.add_argument("--model", default="workbuddy/deepseek-v4.1-flash")
    opts = parser.parse_args()
    if not os.environ.get("LOOM_API_KEY"):
        parser.error("Set LOOM_API_KEY in the process environment.")
    with tempfile.TemporaryDirectory(prefix="loom-magpie-") as folder:
        root = Path(folder)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "input.json").write_text('{"values":[17,25,9]}', encoding="utf-8")
        verify_code = "import json; assert json.load(open('result.json')) == {'sum': 51}; print('VERIFIED_51')"
        verify_argv = [sys.executable, "-c", verify_code]
        args = build_parser().parse_args([
            "--provider", "openai-compatible", "--base-url", opts.base_url,
            "--model", opts.model, "--home", str(root / "state"), "--no-vision",
            "--timeout", "60",
        ])
        runtime, _, _ = _build_runtime(args)
        runtime.limits = AgentLimits(max_model_steps=12, max_tool_calls=20)
        events = []
        cancel_timer = None
        cancel_armed = False

        def observe(event):
            nonlocal cancel_timer, cancel_armed
            events.append(event)
            print(event.kind.value, event.data.get("tool", ""),
                  event.data.get("arguments", "") if event.kind is AgentEventKind.TOOL_REQUESTED else "",
                  flush=True)
            if cancel_armed and event.kind is AgentEventKind.MODEL_REQUESTED:
                cancel_armed = False
                cancel_timer = threading.Timer(1.0, runtime.cancel, args=(session.session_id,))
                cancel_timer.start()

        runtime.subscribe(observe)
        session = _create_workspace_session(runtime, workspace, PermissionMode.WORKSPACE)
        result = runtime.start_turn(session.session_id,
            "Read input.json using tools, sum its values, write result.json with "
            '{"sum":the_total}, then call exec with this exact argv array (no shell): '
            f"{json.dumps(verify_argv)} and cwd={json.dumps(str(workspace))}. "
            "Use only this workspace. Report the result.")
        approvals = 0
        verified = False
        while result.status is AgentStatus.WAITING_APPROVAL:
            approvals += 1
            assert approvals <= 5, "Too many approval attempts"
            pending = runtime.get_session(session.session_id).pending_tool_calls[0]
            approved = (
                pending.name == "exec" and pending.arguments.get("argv") == verify_argv
                and Path(pending.arguments.get("cwd", workspace)).resolve() == workspace.resolve()
            )
            verified = verified or approved
            result = runtime.resume_approval(session.session_id, pending.call_id, approved=approved)
        assert result.status is AgentStatus.COMPLETED, (result.status, result.error)
        assert json.loads((workspace / "result.json").read_text(encoding="utf-8")) == {"sum": 51}
        assert any(e.kind is AgentEventKind.TOOL_COMPLETED for e in events)
        assert verified and any(
            e.kind is AgentEventKind.TOOL_COMPLETED and e.data.get("tool") == "exec"
            and "VERIFIED_51" in str(e.data.get("content", "")) for e in events
        ), "Verification command did not complete successfully"
        print("FILE_TASK_PASS", result.final_text, flush=True)
        result = runtime.start_turn(session.session_id,
            "Without reading any files or calling tools, what were the three original "
            "input numbers and their sum from our previous turn?")
        assert result.status is AgentStatus.COMPLETED, (result.status, result.error)
        assert all(str(n) in result.final_text for n in (17, 25, 9, 51)), result.final_text
        print("CONTEXT_PASS", result.final_text, flush=True)
        cancel_armed = True
        started = time.monotonic()
        result = runtime.start_turn(session.session_id,
            "Write a detailed 5000-word explanation of sorting algorithms. Do not use tools.")
        if cancel_timer:
            cancel_timer.join(timeout=5)
        assert result.status is AgentStatus.CANCELLED, (result.status, result.error)
        elapsed = time.monotonic() - started
        assert elapsed < 15, elapsed
        print("CANCEL_PASS", round(elapsed, 2), flush=True)
        result = runtime.start_turn(session.session_id, "What was the sum in result.json? Answer briefly.")
        assert result.status is AgentStatus.COMPLETED, (result.status, result.error)
        assert "51" in result.final_text, result.final_text
        print("RESUME_PASS", result.final_text, flush=True)


if __name__ == "__main__":
    main()
