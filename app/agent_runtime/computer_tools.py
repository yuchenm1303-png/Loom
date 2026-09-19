from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .computer_types import ComputerAction, ComputerActionType
from .contracts import AgentEventKind, ToolEffect
from .tools import AgentTool, ToolContext, ToolResult

if TYPE_CHECKING:
    from .computer_runtime import ComputerSessionStore, ComputerStepOutcome, ComputerUseRuntime


def _schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        payload["required"] = list(required)
    return payload


def _action_schema() -> dict[str, Any]:
    point = _schema(
        {
            "x": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "y": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        },
        ("x", "y"),
    )
    return _schema(
        {
            "type": {
                "type": "string",
                "enum": [
                    "click",
                    "double_click",
                    "right_click",
                    "move",
                    "drag",
                    "scroll",
                    "type",
                    "hotkey",
                    "key",
                    "switch_window",
                    "wait",
                ],
            },
            "point": point,
            "end_point": point,
            "control_id": {"type": "string", "maxLength": 128},
            "window_id": {"type": "string", "maxLength": 128},
            "text": {"type": "string", "maxLength": 20000},
            "keys": {
                "type": "array",
                "items": {"type": "string", "maxLength": 64},
                "maxItems": 8,
            },
            "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "minimum": 1, "maximum": 10000},
            "duration_ms": {"type": "integer", "minimum": 0, "maximum": 30000},
        },
        ("type",),
    )


def _store(runtime: "ComputerUseRuntime") -> "ComputerSessionStore":
    store = runtime.computer_sessions
    if store is None:
        raise RuntimeError("Computer Use is unavailable; install Loom with the computer extra on Windows")
    return store


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000.0, 3)


def _diagnostics(runtime: "ComputerUseRuntime") -> Any:
    return getattr(runtime, "computer_diagnostics", None)


def _trace(
    runtime: "ComputerUseRuntime",
    context: ToolContext,
    event: str,
    *,
    operation_id: str,
    tool_name: str,
    started: float | None = None,
    **data: Any,
) -> str:
    diagnostics = _diagnostics(runtime)
    trace = getattr(diagnostics, "trace", None)
    if not callable(trace):
        return ""
    payload = {
        "tool_name": tool_name,
        "permission_mode": context.permission_mode,
        "workspace": str(context.workspace),
        **data,
    }
    if started is not None:
        payload["duration_ms"] = _elapsed_ms(started)
    try:
        return str(trace(context.session_id, context.turn_id, event, operation_id=operation_id, **payload))
    except Exception:
        return ""


def _trace_meta(runtime: "ComputerUseRuntime", context: ToolContext) -> dict[str, object]:
    diagnostics = _diagnostics(runtime)
    trace_path = getattr(diagnostics, "trace_path", None)
    status = getattr(diagnostics, "status", None)
    payload: dict[str, object] = {}
    if callable(trace_path):
        try:
            payload["trace_path"] = str(trace_path(context.session_id, context.turn_id))
        except Exception:
            pass
    if callable(status):
        try:
            payload["diagnostics"] = status()
        except Exception:
            pass
    return payload


def _safe_arguments(runtime: "ComputerUseRuntime", arguments: dict[str, Any]) -> dict[str, Any]:
    diagnostics = _diagnostics(runtime)
    raw_allowed = bool(getattr(diagnostics, "raw", False))
    copied = dict(arguments)
    action = copied.get("action")
    if isinstance(action, dict):
        action_copy = dict(action)
        if "text" in action_copy:
            text = str(action_copy.get("text") or "")
            action_copy["text"] = text if raw_allowed else "[TRANSIENT_TEXT]"
            action_copy["text_length"] = len(text)
        copied["action"] = action_copy
    for key in ("instruction", "task", "stop_when"):
        if key in copied:
            text = str(copied.get(key) or "")
            copied[key] = text if raw_allowed else {"length": len(text)}
    return copied


def _active_window_summary(value: Any) -> dict[str, object] | None:
    if value is None:
        return None
    to_dict = getattr(value, "to_dict", None)
    if not callable(to_dict):
        return None
    try:
        payload = dict(to_dict())
    except Exception:
        return None
    for key in ("title", "process_name"):
        if key in payload:
            payload[key] = str(payload.get(key) or "")[:500]
    return payload


def _observation_summary(snapshot: Any) -> dict[str, object]:
    observation = getattr(snapshot, "observation", None)
    frame = getattr(observation, "frame", None)
    return {
        "state_revision": int(getattr(snapshot, "state_revision", 0) or 0),
        "observation_id": str(getattr(observation, "observation_id", "") or ""),
        "image_sha256": str(getattr(observation, "image_sha256", "") or ""),
        "image_bytes": len(bytes(getattr(observation, "image_data", b"") or b"")),
        "frame": frame.to_dict() if hasattr(frame, "to_dict") else None,
        "active_window": _active_window_summary(getattr(observation, "active_window", None)),
        "windows_total": len(tuple(getattr(observation, "windows", ()) or ())),
        "controls_total": len(tuple(getattr(observation, "controls", ()) or ())),
    }


def _point_geometry(outcome: "ComputerStepOutcome") -> dict[str, object]:
    action = outcome.prediction.action
    frame = outcome.before.observation.frame
    payload: dict[str, object] = {}
    if action.point is not None:
        screen_x, screen_y = frame.to_screen(action.point)
        payload["point"] = action.point.to_dict()
        payload["screen_point"] = {"x": screen_x, "y": screen_y}
    if action.end_point is not None:
        screen_x, screen_y = frame.to_screen(action.end_point)
        payload["end_point"] = action.end_point.to_dict()
        payload["screen_end_point"] = {"x": screen_x, "y": screen_y}
    return payload


def _outcome_summary(outcome: "ComputerStepOutcome") -> dict[str, object]:
    execution = outcome.execution.to_safe_dict() if outcome.execution is not None else None
    return {
        "before": _observation_summary(outcome.before),
        "after": _observation_summary(outcome.after) if outcome.after is not None else None,
        "action": outcome.prediction.action.safe_dict(),
        "terminal": outcome.prediction.action.type in {ComputerActionType.FINISH, ComputerActionType.CALL_USER},
        "execution": execution,
        "verification": dict(outcome.verification),
        "geometry": _point_geometry(outcome),
    }


def _enrich_outcome_payload(
    runtime: "ComputerUseRuntime",
    context: ToolContext,
    outcome: "ComputerStepOutcome",
) -> dict[str, object]:
    payload = outcome.to_safe_dict()
    payload["geometry"] = _point_geometry(outcome)
    trace_meta = _trace_meta(runtime, context)
    if trace_meta:
        payload["trace"] = trace_meta
    return payload


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _is_terminal_outcome(outcome: "ComputerStepOutcome") -> bool:
    return outcome.prediction.action.type in {ComputerActionType.FINISH, ComputerActionType.CALL_USER}


def _task_step_content(outcome: "ComputerStepOutcome") -> str:
    action = outcome.prediction.action.type.value
    if outcome.prediction.action.type is ComputerActionType.CALL_USER:
        return "Computer task runner stopped because visual policy requested user assistance."
    if outcome.prediction.action.type is ComputerActionType.FINISH:
        return "Computer task runner stopped because visual policy marked the task complete."
    if bool(outcome.verification.get("stuck_detected")):
        return "Computer task runner detected a repeated unchanged action and is asking the policy to try a different strategy."
    if outcome.execution is not None and not outcome.execution.ok:
        return f"Computer task runner attempted {action}, but execution did not complete cleanly."
    return f"Computer task runner executed one {action} step and re-observed the desktop."


def _task_step_summary(index: int, outcome: "ComputerStepOutcome") -> dict[str, object]:
    execution_ok = True if outcome.execution is None else bool(outcome.execution.ok)
    verification = dict(outcome.verification)
    return {
        "index": index,
        "action": outcome.prediction.action.safe_dict(),
        "execution_ok": execution_ok,
        "terminal": _is_terminal_outcome(outcome),
        "verification": {
            "method": verification.get("method"),
            "visual_changed": verification.get("visual_changed"),
            "active_window_changed": verification.get("active_window_changed"),
            "target_confirmed": verification.get("target_confirmed"),
            "stuck_detected": verification.get("stuck_detected", False),
            "revision_autofixed": verification.get("revision_autofixed", False),
        },
        "geometry": _point_geometry(outcome),
        "before": _observation_summary(outcome.before),
        "after": _observation_summary(outcome.after) if outcome.after is not None else None,
    }


def _task_instruction(
    *,
    task: str,
    stop_when: str,
    step_index: int,
    max_steps: int,
    previous: dict[str, object] | None,
    previous_error: str,
) -> str:
    parts = [
        "You are inside Loom computer_run_task, a continuous desktop automation loop.",
        "Complete the user's GUI task by choosing exactly one next action from the screenshot.",
        "Do not explain in prose unless you need to call the user or terminate.",
        f"Task: {task}",
        f"Step budget: {step_index}/{max_steps}.",
    ]
    if stop_when:
        parts.append(f"Stop condition: {stop_when}")
    if previous is not None:
        action = previous.get("action")
        verification = previous.get("verification")
        parts.append(f"Previous step summary: action={action}; verification={verification}.")
        if isinstance(verification, dict) and verification.get("stuck_detected"):
            parts.append(
                "The last action repeated on an unchanged screenshot. Choose a different strategy now: use keyboard shortcuts, click a different visible target, scroll, wait, or ask the user."
            )
    if previous_error:
        parts.append(
            f"Previous policy/execution error was normalized by Loom: {previous_error[:500]}. Try again with one supported visible action."
        )
    parts.append("When the visible task is done, return terminate success. When you need user help, return interact/answer/call_user.")
    return "\n".join(parts)


def _emit_inner_tool_event(
    context: ToolContext,
    kind: AgentEventKind,
    *,
    call_id: str,
    tool_name: str,
    step_index: int,
    arguments: dict[str, Any] | None = None,
    result: ToolResult | None = None,
    content: str = "",
) -> None:
    data: dict[str, object] = {
        "call_id": call_id,
        "tool": tool_name,
        "source": "computer_run_task",
        "task_step_index": step_index,
    }
    if arguments is not None:
        data["arguments"] = arguments
    if result is not None:
        data["ok"] = result.ok
        data["content"] = result.content
        data["data"] = result.data
    elif content:
        data["content"] = content
    try:
        context.emit(kind, data)
    except Exception:
        pass


def computer_tools(runtime: "ComputerUseRuntime") -> tuple[AgentTool, ...]:
    def status(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        operation_id = _diagnostics(runtime).operation_id() if _diagnostics(runtime) is not None else uuid.uuid4().hex[:16]
        started = time.perf_counter()
        data = runtime.computer_status(context.session_id)
        data["trace"] = _trace_meta(runtime, context)
        _trace(
            runtime,
            context,
            "status.read",
            operation_id=operation_id,
            tool_name="computer_status",
            started=started,
            status="completed",
            result=data,
        )
        return ToolResult(
            ok=True,
            content="Computer Use runtime status.",
            data=data,
        )

    tools: list[AgentTool] = [
        AgentTool(
            name="computer_status",
            description=(
                "Report Loom Computer Use availability, Windows operator/grounding backend, observation mode, "
                "diagnostic log paths, and security/verification limitations. This does not capture the screen or inject input."
            ),
            input_schema=_schema({}),
            handler=status,
            effect=ToolEffect.READ_ONLY,
        )
    ]
    if runtime.computer_sessions is None:
        return tuple(tools)

    def observe(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        diagnostics = _diagnostics(runtime)
        operation_id = diagnostics.operation_id() if diagnostics is not None else uuid.uuid4().hex[:16]
        started = time.perf_counter()
        _trace(
            runtime,
            context,
            "observe.started",
            operation_id=operation_id,
            tool_name="computer_observe",
            status="started",
            arguments=_safe_arguments(runtime, arguments),
        )
        try:
            snapshot = _store(runtime).observe(context.session_id)
            data = snapshot.to_safe_dict(control_limit=int(arguments.get("control_limit", 80)))
            if bool(arguments.get("save_screenshot", False)):
                raw_path = str(arguments.get("path") or "").strip()
                # The capture profile decides the encoding, so the caller's
                # extension has to agree with the bytes actually being written.
                suffix = snapshot.observation.image_suffix
                if raw_path:
                    relative = Path(raw_path)
                    if relative.suffix.casefold() != suffix:
                        raise ValueError(
                            f"computer_observe screenshot path must end in {suffix} "
                            "for the active capture profile"
                        )
                else:
                    relative = Path("computer-screenshots") / f"{uuid.uuid4().hex[:16]}{suffix}"
                target = context.resolve_workspace_path(relative.as_posix())
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(snapshot.observation.image_data)
                data["screenshot_path"] = relative.as_posix()
            data["trace"] = _trace_meta(runtime, context)
            _trace(
                runtime,
                context,
                "observe.completed",
                operation_id=operation_id,
                tool_name="computer_observe",
                started=started,
                status="completed",
                observation=_observation_summary(snapshot),
                result={
                    "state_revision": snapshot.state_revision,
                    "observation_id": snapshot.observation.observation_id,
                    "image_sha256": snapshot.observation.image_sha256,
                    "screenshot_path": data.get("screenshot_path", ""),
                },
            )
            return ToolResult(
                ok=True,
                content=(
                    "Computer observation captured. The returned state_revision can be reused for deterministic "
                    "computer_action calls; Loom keeps a short same-session revision history to avoid unnecessary stale-state failures."
                ),
                data=data,
            )
        except Exception as exc:
            _trace(
                runtime,
                context,
                "observe.failed",
                operation_id=operation_id,
                tool_name="computer_observe",
                started=started,
                status="failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

    def act(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        diagnostics = _diagnostics(runtime)
        operation_id = diagnostics.operation_id() if diagnostics is not None else uuid.uuid4().hex[:16]
        started = time.perf_counter()
        _trace(
            runtime,
            context,
            "action.started",
            operation_id=operation_id,
            tool_name="computer_action",
            status="started",
            arguments=_safe_arguments(runtime, arguments),
        )
        try:
            raw_action = arguments.get("action")
            if not isinstance(raw_action, dict):
                raise ValueError("computer_action action must be an object")
            action_payload = dict(raw_action)
            if str(action_payload.get("type") or "") == "type" and "text" in action_payload:
                action_payload["text"] = runtime.consume_computer_transient(str(action_payload.get("text") or ""), context.session_id)
            action = ComputerAction.from_dict(action_payload)
            outcome = _store(runtime).execute(
                context.session_id,
                int(arguments["state_revision"]),
                action,
            )
            data = _enrich_outcome_payload(runtime, context, outcome)
            _trace(
                runtime,
                context,
                "action.completed",
                operation_id=operation_id,
                tool_name="computer_action",
                started=started,
                status="completed" if bool(outcome.execution and outcome.execution.ok) else "failed",
                outcome=_outcome_summary(outcome),
            )
            content = "Computer action executed and the desktop was re-observed."
            if bool(outcome.verification.get("revision_autofixed")):
                content = "Computer action executed after Loom reused a compatible same-session observation revision."
            return ToolResult(
                ok=bool(outcome.execution and outcome.execution.ok),
                content=content,
                data=data,
            )
        except Exception as exc:
            _trace(
                runtime,
                context,
                "action.failed",
                operation_id=operation_id,
                tool_name="computer_action",
                started=started,
                status="failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

    def step(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        diagnostics = _diagnostics(runtime)
        operation_id = diagnostics.operation_id() if diagnostics is not None else uuid.uuid4().hex[:16]
        started = time.perf_counter()
        _trace(
            runtime,
            context,
            "step.started",
            operation_id=operation_id,
            tool_name="computer_step",
            status="started",
            grounder=getattr(runtime, "computer_grounder_name", ""),
            arguments=_safe_arguments(runtime, arguments),
        )
        try:
            instruction = runtime.consume_computer_transient(str(arguments["instruction"]), context.session_id)
            if len(instruction) > 20_000:
                raise ValueError("computer_step instruction exceeds 20,000 characters")
            outcome = _store(runtime).step(context.session_id, instruction)
            terminal = outcome.prediction.action.type.value
            if bool(outcome.verification.get("stuck_detected")):
                content = "Computer visual policy paused after a repeated unchanged action so the outer agent can re-plan instead of failing."
            elif terminal == "call_user":
                content = "Computer visual policy requested user assistance."
            elif terminal == "finish":
                content = "Computer visual policy considers the current GUI instruction complete."
            else:
                content = "Computer visual policy executed one action and the desktop was re-observed."
            data = _enrich_outcome_payload(runtime, context, outcome)
            _trace(
                runtime,
                context,
                "step.completed",
                operation_id=operation_id,
                tool_name="computer_step",
                started=started,
                status="completed" if bool(outcome.execution is None or outcome.execution.ok) else "failed",
                grounder=getattr(runtime, "computer_grounder_name", ""),
                outcome=_outcome_summary(outcome),
                prediction={
                    "action": outcome.prediction.action.safe_dict(),
                    "thought": outcome.prediction.thought if bool(getattr(diagnostics, "raw", False)) else {"length": len(outcome.prediction.thought)},
                },
            )
            return ToolResult(
                ok=bool(outcome.execution is None or outcome.execution.ok),
                content=content,
                data=data,
            )
        except Exception as exc:
            _trace(
                runtime,
                context,
                "step.failed",
                operation_id=operation_id,
                tool_name="computer_step",
                started=started,
                status="failed",
                grounder=getattr(runtime, "computer_grounder_name", ""),
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise

    def run_task(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        diagnostics = _diagnostics(runtime)
        operation_id = diagnostics.operation_id() if diagnostics is not None else uuid.uuid4().hex[:16]
        started = time.perf_counter()
        max_steps = _bounded_int(arguments.get("max_steps"), default=8, minimum=1, maximum=40)
        max_retries = _bounded_int(arguments.get("max_retries"), default=2, minimum=0, maximum=5)
        task = runtime.consume_computer_transient(str(arguments["task"]), context.session_id)
        stop_when = runtime.consume_computer_transient(str(arguments.get("stop_when") or ""), context.session_id)
        if not task.strip():
            raise ValueError("computer_run_task task must not be empty")
        if len(task) > 20_000:
            raise ValueError("computer_run_task task exceeds 20,000 characters")
        if len(stop_when) > 4_000:
            raise ValueError("computer_run_task stop_when exceeds 4,000 characters")

        _trace(
            runtime,
            context,
            "task.started",
            operation_id=operation_id,
            tool_name="computer_run_task",
            started=started,
            status="started",
            max_steps=max_steps,
            max_retries=max_retries,
            arguments=_safe_arguments(runtime, {"task": task, "stop_when": stop_when, "max_steps": max_steps}),
        )

        steps: list[dict[str, object]] = []
        previous: dict[str, object] | None = None
        previous_error = ""
        retry_count = 0
        status = "max_steps_reached"
        final_content = "Computer task runner reached the step budget and returned control to the outer agent."

        for index in range(1, max_steps + 1):
            context.raise_if_cancelled()
            instruction = _task_instruction(
                task=task,
                stop_when=stop_when,
                step_index=index,
                max_steps=max_steps,
                previous=previous,
                previous_error=previous_error,
            )
            inner_call_id = f"computer-run-task:{operation_id}:{index}"
            safe_instruction_args = _safe_arguments(runtime, {"instruction": instruction})
            _trace(
                runtime,
                context,
                "task.step_started",
                operation_id=operation_id,
                tool_name="computer_run_task",
                status="started",
                task_step_index=index,
                nested_tool="computer_step",
                arguments=safe_instruction_args,
            )
            _emit_inner_tool_event(
                context,
                AgentEventKind.TOOL_STARTED,
                call_id=inner_call_id,
                tool_name="computer_step",
                step_index=index,
                arguments=safe_instruction_args,
            )

            try:
                outcome = _store(runtime).step(context.session_id, instruction)
            except Exception as exc:
                previous_error = f"{type(exc).__name__}: {exc}"
                retry_count += 1
                _trace(
                    runtime,
                    context,
                    "task.step_failed",
                    operation_id=operation_id,
                    tool_name="computer_run_task",
                    started=started,
                    status="failed",
                    task_step_index=index,
                    nested_tool="computer_step",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    retry_count=retry_count,
                )
                _emit_inner_tool_event(
                    context,
                    AgentEventKind.TOOL_FAILED,
                    call_id=inner_call_id,
                    tool_name="computer_step",
                    step_index=index,
                    content=previous_error,
                )
                if retry_count <= max_retries:
                    continue
                status = "needs_outer_replan"
                final_content = "Computer task runner stopped after repeated visual policy errors and returned control for re-planning."
                break

            retry_count = 0
            previous_error = ""
            summary = _task_step_summary(index, outcome)
            steps.append(summary)
            previous = summary
            data = _enrich_outcome_payload(runtime, context, outcome)
            result = ToolResult(
                ok=bool(outcome.execution is None or outcome.execution.ok),
                content=_task_step_content(outcome),
                data=data,
            )
            _trace(
                runtime,
                context,
                "task.step_completed",
                operation_id=operation_id,
                tool_name="computer_run_task",
                started=started,
                status="completed" if result.ok else "execution_failed",
                task_step_index=index,
                nested_tool="computer_step",
                outcome=summary,
            )
            _emit_inner_tool_event(
                context,
                AgentEventKind.TOOL_COMPLETED if result.ok else AgentEventKind.TOOL_FAILED,
                call_id=inner_call_id,
                tool_name="computer_step",
                step_index=index,
                result=result,
            )

            if outcome.prediction.action.type is ComputerActionType.FINISH:
                status = "completed"
                final_content = "Computer task runner completed the visible GUI task."
                break
            if outcome.prediction.action.type is ComputerActionType.CALL_USER:
                status = "needs_user"
                final_content = "Computer task runner needs user help before continuing."
                break
            if bool(outcome.verification.get("stuck_detected")) and sum(
                1 for item in steps[-3:] if isinstance(item.get("verification"), dict) and item["verification"].get("stuck_detected")
            ) >= 2:
                status = "needs_outer_replan"
                final_content = "Computer task runner stopped after repeated unchanged actions and returned control for re-planning."
                break

        trace_meta = _trace_meta(runtime, context)
        payload: dict[str, object] = {
            "status": status,
            "step_count": len(steps),
            "max_steps": max_steps,
            "steps": steps[-12:],
            "truncated_steps": max(0, len(steps) - 12),
            "trace": trace_meta,
        }
        _trace(
            runtime,
            context,
            "task.completed",
            operation_id=operation_id,
            tool_name="computer_run_task",
            started=started,
            status=status,
            step_count=len(steps),
            max_steps=max_steps,
            trace=trace_meta,
        )
        return ToolResult(ok=True, content=final_content, data=payload)

    sensitive = ToolEffect.SENSITIVE
    tools.extend(
        [
            AgentTool(
                name="computer_observe",
                description=(
                    "Capture the foreground Windows application as a physical-pixel frame, enumerate visible windows and "
                    "UI Automation controls, write a per-turn Computer Use trace, and return a bounded sanitized observation "
                    "with a state_revision. Use this before deterministic computer_action calls."
                ),
                input_schema=_schema(
                    {
                        "control_limit": {"type": "integer", "minimum": 0, "maximum": 200},
                        "save_screenshot": {"type": "boolean"},
                        "path": {"type": "string", "maxLength": 1000},
                    }
                ),
                handler=observe,
                effect=sensitive,
            ),
            AgentTool(
                name="computer_action",
                description=(
                    "Execute exactly one typed Windows GUI action. Prefer a recent state_revision from computer_observe, "
                    "but Loom keeps a short same-session revision history so harmless observe/status drift does not block "
                    "execution. Prefer control_id for UIA-native clicks/edits; normalized point coordinates are frame-local "
                    "0..1 fallbacks. The result includes replay trace metadata and resolved screen coordinates when available."
                ),
                input_schema=_schema(
                    {
                        "state_revision": {"type": "integer", "minimum": 1},
                        "action": _action_schema(),
                    },
                    ("state_revision", "action"),
                ),
                handler=act,
                effect=sensitive,
            ),
        ]
    )
    if runtime.computer_sessions.grounder is not None:
        tools.extend(
            [
                AgentTool(
                    name="computer_run_task",
                    description=(
                        "Run a continuous screenshot-driven desktop automation task inside Loom. Prefer this for user-level "
                        "GUI tasks such as operating WeChat, Edge, apps, settings, or multi-step desktop workflows. The tool "
                        "internally loops observe -> visual grounding -> normalize action -> execute -> verify, keeps the HUD "
                        "and trace continuous, and returns control only when the task is complete, needs the user, hits the step "
                        "budget, or needs outer re-planning. This avoids brittle one-tool-call-per-click orchestration."
                    ),
                    input_schema=_schema(
                        {
                            "task": {"type": "string", "minLength": 1, "maxLength": 20000},
                            "stop_when": {"type": "string", "maxLength": 4000},
                            "max_steps": {"type": "integer", "minimum": 1, "maximum": 40},
                            "max_retries": {"type": "integer", "minimum": 0, "maximum": 5},
                        },
                        ("task",),
                    ),
                    handler=run_task,
                    effect=sensitive,
                ),
                AgentTool(
                    name="computer_step",
                    description=(
                        "Perform one low-level screenshot-driven GUI policy step: capture screenshot + UIA context, ask the "
                        "configured visual grounding backend for one next action, normalize provider-specific tool-call variants, "
                        "execute at most one OS action, then re-observe and return verification signals. Prefer computer_run_task "
                        "for normal multi-step GUI work; use this only for surgical debugging or a single controlled action."
                    ),
                    input_schema=_schema(
                        {"instruction": {"type": "string", "minLength": 1, "maxLength": 20000}},
                        ("instruction",),
                    ),
                    handler=step,
                    effect=sensitive,
                ),
            ]
        )
    return tuple(tools)


__all__ = ["computer_tools"]
