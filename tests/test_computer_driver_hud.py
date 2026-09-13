from __future__ import annotations

from pathlib import Path

from app.agent_runtime.computer_driver import ComputerDriverEvent
from app.agent_runtime.computer_driver_runtime import ComputerDriverRuntime, _safe_driver_data
from app.agent_runtime.contracts import AgentEventKind
from app.agent_runtime.tools import (
    AgentTool,
    ToolContext,
    ToolExposure,
    ToolRegistry,
    ToolResult,
)


def _stub_tool(name: str) -> AgentTool:
    return AgentTool(
        name=name,
        description=f"stub {name}",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda _context, _arguments: ToolResult(True, "ok", {}),
    )


def test_driver_result_redacts_stderr_and_provider_payloads():
    safe = _safe_driver_data(
        {
            "engine": "ufo2",
            "stderr_tail": "secret task text from provider stderr",
            "error": "provider echoed the user request",
            "preview": "raw provider response",
            "traceback": "raw traceback with task text",
            "nested": {"text": "typed secret", "status": "failed"},
        }
    )

    # stderr_tail is scrubbed line by line rather than blanked, so a real
    # traceback keeps its frames. Text that is not traceback-shaped, like this,
    # still goes.
    assert safe["stderr_tail"] == "[REDACTED_UFO_STDERR]"
    assert safe["error"] == "[REDACTED_DRIVER_DATA]"
    assert safe["preview"] == "[REDACTED_DRIVER_DATA]"
    assert safe["traceback"] == "[REDACTED_DRIVER_DATA]"
    assert safe["nested"]["text"] == "[REDACTED_DRIVER_DATA]"
    assert safe["nested"]["status"] == "failed"
    assert "secret task text" not in repr(safe)
    assert "typed secret" not in repr(safe)


def test_mature_driver_keeps_low_level_computer_tools_deferred():
    runtime = object.__new__(ComputerDriverRuntime)
    runtime.computer_driver_mode = "ufo"
    runtime.computer_driver = object()
    runtime.tools = ToolRegistry(
        tuple(
            _stub_tool(name)
            for name in (
                "computer_status",
                "computer_observe",
                "computer_action",
                "computer_step",
                "computer_run_task",
            )
        )
    )

    runtime._install_driver_task_tool()

    direct_names = {
        tool.name
        for tool in runtime.tools.router(capability_settings={}).all()
    }
    deferred = {
        tool.name: tool.exposure
        for tool in runtime.tools.deferred(capability_settings={})
    }
    assert "computer_status" in direct_names
    assert "computer_run_task" in direct_names
    assert "computer_observe" not in direct_names
    assert "computer_action" not in direct_names
    assert "computer_step" not in direct_names
    assert deferred == {
        "computer_action": ToolExposure.DEFERRED,
        "computer_observe": ToolExposure.DEFERRED,
        "computer_step": ToolExposure.DEFERRED,
    }


def test_legacy_mode_preserves_direct_low_level_computer_tools():
    runtime = object.__new__(ComputerDriverRuntime)
    runtime.computer_driver_mode = "legacy"
    runtime.computer_driver = None
    runtime.tools = ToolRegistry(
        tuple(
            _stub_tool(name)
            for name in (
                "computer_status",
                "computer_observe",
                "computer_action",
                "computer_step",
                "computer_run_task",
            )
        )
    )

    runtime._install_driver_task_tool()

    direct_names = {
        tool.name
        for tool in runtime.tools.router(capability_settings={}).all()
    }
    assert {
        "computer_status",
        "computer_observe",
        "computer_action",
        "computer_step",
        "computer_run_task",
    }.issubset(direct_names)


def test_driver_action_has_balanced_transcript_lifecycle_without_terminal_hud_identity(tmp_path: Path):
    emitted: list[tuple[AgentEventKind, dict[str, object]]] = []
    context = ToolContext(
        session_id="session-1",
        turn_id="turn-1",
        workspace=tmp_path,
        emit_event=lambda kind, data: emitted.append((kind, data)),
    )
    pending: list[str | None] = [None]

    ComputerDriverRuntime._emit_action_event(
        context,
        ComputerDriverEvent(
            task_id="task-1",
            sequence=1,
            kind="action.started",
            data={
                "action": "click_on_coordinates",
                "parameters": {"x": 0.5, "y": 0.25},
                "window": {"name": "WeChat"},
                "hud_point": {"x_norm": 0.65, "y_norm": 0.4},
            },
        ),
        pending,
    )

    assert [kind for kind, _ in emitted] == [
        AgentEventKind.TOOL_REQUESTED,
        AgentEventKind.TOOL_STARTED,
    ]
    requested = emitted[0][1]
    started = emitted[1][1]
    assert requested["tool"] == "computer_action"
    assert started["tool"] == "computer_action"
    assert requested["call_id"] == started["call_id"]
    assert requested["nested"] is True
    assert requested["driver_progress"] is True
    assert requested["arguments"]["action"]["point"] == {"x": 0.65, "y": 0.4}
    assert pending[0] is not None

    ComputerDriverRuntime._emit_action_event(
        context,
        ComputerDriverEvent(
            task_id="task-1",
            sequence=2,
            kind="action.completed",
            data={
                "action": "click_on_coordinates",
                "result": {"ok": True, "status": "success"},
                "window": {"name": "WeChat"},
            },
        ),
        pending,
    )

    assert len(emitted) == 3
    kind, completed = emitted[-1]
    assert kind is AgentEventKind.TOOL_COMPLETED
    assert completed["call_id"] == requested["call_id"]
    # app_server.py closes tool transcript items by call_id, so the completion
    # can use a non-computer identity. The HUD ignores it because only
    # computer_* tool names drive Computer Use presentation.
    assert completed["tool"] == "driver_action_result"
    assert completed["driver_progress"] is True
    assert pending[0] is None


def test_prescrubbed_stderr_survives_redaction_but_raw_fields_do_not():
    """A driver failure has to leave something to act on.

    stderr_tail arrives already rewritten by the driver's whitelist scrubber,
    which keeps only fixed traceback headers, file basenames, line numbers,
    function names and exception class names. Redacting it a second time
    destroyed exactly that structure, so a TypeError inside UFO reached the logs
    as a bare error_type with no frames and could not be diagnosed at all.
    """

    scrubbed = (
        'Traceback (most recent call last):\n'
        '  File "session.py", line 912, in handle\n'
        'TypeError: [REDACTED_EXCEPTION_MESSAGE]'
    )
    safe = _safe_driver_data(
        {
            "error_type": "TypeError",
            "frames": ["session.py:912:handle", "host_agent.py:233:process"],
            "stderr_tail": scrubbed,
            "error": "provider echoed the user request",
            "traceback": "raw traceback carrying task text",
            "task": "open WeChat and search for today's news",
        }
    )

    assert safe["stderr_tail"] == scrubbed
    assert safe["frames"] == ["session.py:912:handle", "host_agent.py:233:process"]
    # Everything that was never scrubbed at the source still is here.
    assert safe["error"] == "[REDACTED_DRIVER_DATA]"
    assert safe["traceback"] == "[REDACTED_DRIVER_DATA]"
    assert safe["task"] == "[REDACTED_DRIVER_DATA]"
    assert "WeChat" not in repr(safe)


def test_the_stderr_scrubber_emits_no_free_text():
    """What makes passing stderr_tail through safe."""

    from app.agent_runtime.computer_ufo_driver import _safe_stderr_line

    assert _safe_stderr_line("Traceback (most recent call last):") == "Traceback (most recent call last):"
    assert (
        _safe_stderr_line(r'  File "C:\secret\path\session.py", line 912, in handle')
        == '  File "session.py", line 912, in handle'
    )
    assert (
        _safe_stderr_line("TypeError: cannot use 'NoneType' as a control label")
        == "TypeError: [REDACTED_EXCEPTION_MESSAGE]"
    )
    # Anything the scrubber does not recognise is discarded rather than passed on.
    assert _safe_stderr_line("user typed: hunter2") == "[REDACTED_UFO_STDERR]"
    assert _safe_stderr_line("INFO: opening C:/private/report.docx") == "INFO: [REDACTED_UFO_STDERR]"


def test_sidecar_frames_carry_location_without_payload():
    import importlib.util
    import pathlib

    spec = importlib.util.spec_from_file_location(
        "_loom_sidecar_probe",
        pathlib.Path(__file__).resolve().parents[1] / "app" / "agent_runtime" / "ufo_sidecar.py",
    )
    # The sidecar imports UFO at module scope, so only the helper's source is
    # exercised here rather than the module.
    source = spec.origin
    text = pathlib.Path(source).read_text(encoding="utf-8")
    namespace: dict = {"traceback": __import__("traceback")}
    start = text.index("def _exception_frames(")
    end = text.index("def _keep_raw_logs(")
    exec(compile(text[start:end], source, "exec"), namespace)
    build = namespace["_exception_frames"]

    try:
        raise TypeError("cannot use 'NoneType' as a control label")
    except TypeError as exc:
        frames = build(exc)

    assert frames
    assert all(len(item.split(":")) == 3 for item in frames)
    assert "NoneType" not in " ".join(frames)
    assert frames[-1].startswith("test_computer_driver_hud.py:")


def test_sidecar_frames_follow_a_chained_cause():
    import importlib.util
    import pathlib

    source = str(
        pathlib.Path(__file__).resolve().parents[1] / "app" / "agent_runtime" / "ufo_sidecar.py"
    )
    text = pathlib.Path(source).read_text(encoding="utf-8")
    namespace: dict = {"traceback": __import__("traceback")}
    start = text.index("def _exception_frames(")
    end = text.index("def _keep_raw_logs(")
    exec(compile(text[start:end], source, "exec"), namespace)
    build = namespace["_exception_frames"]

    def inner():
        raise ValueError("inner detail")

    try:
        try:
            inner()
        except ValueError as cause:
            raise TypeError("outer") from cause
    except TypeError as exc:
        frames = build(exc)

    # UFO wraps failures, so the frame that actually broke is usually in the cause.
    assert any(":inner" in item for item in frames)
