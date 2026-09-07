from __future__ import annotations

from typing import Any, Mapping

from .contracts import AgentEventKind, ToolEffect
from .process_runtime import (
    ManagedProcess,
    ProcessSnapshot,
    ProcessState,
    ProcessStore,
    validate_argv,
    validate_terminal_size,
    validate_timeout,
)
from .tools import AgentTool, ToolContext, ToolResult


_MAX_WAIT_SECONDS = 120.0


def _store(context: ToolContext) -> ProcessStore:
    store = context.service("process_store")
    if not isinstance(store, ProcessStore):
        raise RuntimeError("process runtime service is unavailable")
    return store


def _validate_env(raw: object) -> Mapping[str, object] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("env must be an object of string values")
    if len(raw) > 256:
        raise ValueError("env contains too many entries")
    if any(not isinstance(value, str) for value in raw.values()):
        raise ValueError("env values must be strings")
    return {str(key): value for key, value in raw.items()}


def _resolve_command(context: ToolContext, arguments: dict[str, Any]):
    argv = validate_argv(arguments["argv"])
    relative_cwd = str(arguments.get("cwd") or ".").strip() or "."
    cwd = context.resolve_workspace_path(relative_cwd)
    if not cwd.is_dir():
        raise ValueError("command cwd must be a workspace directory")
    timeout_seconds = validate_timeout(arguments.get("timeout_seconds"))
    stdin_text = str(arguments.get("stdin") or "")
    if len(stdin_text) > 256_000:
        raise ValueError("stdin exceeds 256,000 characters")
    env = _validate_env(arguments.get("env"))
    use_pty = bool(arguments.get("pty", False))
    rows, cols = validate_terminal_size(arguments.get("rows", 24), arguments.get("cols", 80))
    return argv, relative_cwd, cwd, timeout_seconds, stdin_text, env, use_pty, rows, cols


def _result_from_snapshot(snapshot: ProcessSnapshot, *, content_prefix: str = "") -> ToolResult:
    parts: list[str] = []
    if content_prefix:
        parts.append(content_prefix)
    parts.append(
        f"process={snapshot.process_id} status={snapshot.state.value} backend={snapshot.backend}"
    )
    if snapshot.pty:
        parts.append(f"pty={snapshot.rows}x{snapshot.cols}")
    if snapshot.sandbox.enforced:
        parts.append(f"sandbox={snapshot.sandbox.backend.value}:{snapshot.sandbox.mode.value}")
    elif snapshot.sandbox.mode.value != "disabled":
        parts.append(f"sandbox=not-enforced ({snapshot.sandbox.reason})")
    if snapshot.returncode is not None:
        parts.append(f"exit={snapshot.returncode}")
    if snapshot.output_truncated:
        parts.append(
            f"[loom: output is bounded; oldest data was dropped "
            f"({snapshot.dropped_bytes} bytes reported dropped)]"
        )
    if snapshot.stdout:
        label = "terminal" if snapshot.pty else "stdout"
        parts.append(f"{label}:\n{snapshot.stdout}")
    if snapshot.stderr:
        parts.append(f"stderr:\n{snapshot.stderr}")
    if snapshot.failure:
        parts.append(f"runtime failure: {snapshot.failure}")
    ok = snapshot.running or snapshot.returncode == 0
    if snapshot.state in {ProcessState.TIMED_OUT, ProcessState.FAILED}:
        ok = False
    return ToolResult(ok=ok, content="\n".join(parts), data=snapshot.to_dict())


def _emit_started(
    context: ToolContext,
    managed: ManagedProcess,
    *,
    relative_cwd: str,
    background: bool,
) -> None:
    snapshot = managed.snapshot()
    context.emit(
        AgentEventKind.PROCESS_STARTED,
        {
            "process_id": managed.process_id,
            "argv": list(managed.argv),
            "cwd": relative_cwd,
            "background": background,
            "backend": managed.backend.name,
            "pty": managed.backend.pty,
            "rows": snapshot.rows,
            "cols": snapshot.cols,
            "state": snapshot.state.value,
            "sandbox": managed.sandbox.to_dict(),
        },
    )


def _emit_output(context: ToolContext, managed: ManagedProcess, stdout: str, stderr: str) -> None:
    durable_stdout, durable_stderr, truncated = managed.bounded_event_output(stdout, stderr)
    if not durable_stdout and not durable_stderr:
        return
    context.emit(
        AgentEventKind.PROCESS_OUTPUT,
        {
            "process_id": managed.process_id,
            "stdout": durable_stdout,
            "stderr": durable_stderr,
            "pty": managed.backend.pty,
            "truncated": truncated,
        },
    )


def _emit_snapshot_delta(
    context: ToolContext,
    managed: ManagedProcess,
    snapshot: ProcessSnapshot,
) -> None:
    if snapshot.stdout_delta or snapshot.stderr_delta:
        _emit_output(context, managed, snapshot.stdout_delta, snapshot.stderr_delta)


def _emit_exit_once(
    context: ToolContext,
    managed: ManagedProcess,
    snapshot: ProcessSnapshot,
) -> None:
    if snapshot.running or not managed.claim_exit_event():
        return
    context.emit(
        AgentEventKind.PROCESS_EXITED,
        {
            "process_id": snapshot.process_id,
            "returncode": snapshot.returncode,
            "timed_out": snapshot.timed_out,
            "state": snapshot.state.value,
            "backend": snapshot.backend,
            "pty": snapshot.pty,
            "output_truncated": snapshot.output_truncated,
            "dropped_bytes": snapshot.dropped_bytes,
            "failure": snapshot.failure,
            "sandbox": snapshot.sandbox.to_dict(),
        },
    )


def _exec_handler(
    context: ToolContext,
    arguments: dict[str, Any],
    *,
    force_wait: bool | None = None,
) -> ToolResult:
    context.raise_if_cancelled()
    (
        argv,
        relative_cwd,
        cwd,
        timeout_seconds,
        stdin_text,
        env,
        use_pty,
        rows,
        cols,
    ) = _resolve_command(context, arguments)
    should_wait = bool(arguments.get("wait", True)) if force_wait is None else force_wait
    managed = _store(context).start(
        session_id=context.session_id,
        argv=argv,
        cwd=cwd,
        workspace=context.workspace,
        permission_mode=context.permission_mode,
        timeout_seconds=timeout_seconds,
        stdin_text=stdin_text,
        env=env,
        pty=use_pty,
        rows=rows,
        cols=cols,
    )
    _emit_started(context, managed, relative_cwd=relative_cwd, background=not should_wait)
    if not should_wait:
        snapshot = managed.snapshot(drain_delta=True)
        _emit_snapshot_delta(context, managed, snapshot)
        _emit_exit_once(context, managed, snapshot)
        return _result_from_snapshot(snapshot, content_prefix="Started managed process.")

    # Pipe execution keeps subprocess.run-compatible EOF behavior. PTYs are
    # intentionally left interactive; callers can use wait=false + exec_write.
    if not use_pty:
        managed.close_stdin()
    snapshot = managed.wait(
        cancel_check=lambda: context.cancelled,
        on_output=lambda stdout, stderr: _emit_output(context, managed, stdout, stderr),
    )
    _emit_exit_once(context, managed, snapshot)
    if context.cancelled:
        return ToolResult(
            ok=False,
            content="Command cancelled and its process tree was terminated.",
            data=snapshot.to_dict(),
        )
    return _result_from_snapshot(snapshot)


def _exec_schema(*, include_wait: bool = True) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "argv": {"type": "array", "items": {"type": "string"}},
        "cwd": {"type": "string"},
        "stdin": {"type": "string"},
        "env": {"type": "object"},
        "timeout_seconds": {"type": "integer"},
        "pty": {"type": "boolean"},
        "rows": {"type": "integer"},
        "cols": {"type": "integer"},
    }
    if include_wait:
        properties["wait"] = {"type": "boolean"}
    return {
        "type": "object",
        "properties": properties,
        "required": ["argv"],
        "additionalProperties": False,
    }


def exec_tool() -> AgentTool:
    return AgentTool(
        name="exec",
        description=(
            "Unified command execution. Executes argv directly with no implicit shell. By default "
            "waits for exit; set wait=false for a long-running or interactive process. Set pty=true "
            "for a real Unix PTY or Windows ConPTY. cwd must stay inside the workspace. Environment "
            "overrides are explicit and secret-like names are rejected."
        ),
        input_schema=_exec_schema(include_wait=True),
        handler=lambda context, arguments: _exec_handler(context, arguments),
        effect=ToolEffect.SENSITIVE,
    )


def exec_wait_tool() -> AgentTool:
    def wait_process(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        managed = _store(context).get(
            str(arguments["process_id"]),
            session_id=context.session_id,
        )
        raw_wait = arguments.get("wait_timeout_seconds", 30)
        wait_seconds = float(30 if raw_wait is None else raw_wait)
        if not 0 <= wait_seconds <= _MAX_WAIT_SECONDS:
            raise ValueError(
                f"wait_timeout_seconds must be within 0..{int(_MAX_WAIT_SECONDS)}"
            )
        if wait_seconds == 0:
            snapshot = managed.snapshot(drain_delta=True)
            _emit_snapshot_delta(context, managed, snapshot)
        else:
            snapshot = managed.wait(
                cancel_check=lambda: context.cancelled,
                on_output=lambda stdout, stderr: _emit_output(
                    context,
                    managed,
                    stdout,
                    stderr,
                ),
                wait_timeout_seconds=wait_seconds,
            )
        _emit_exit_once(context, managed, snapshot)
        if context.cancelled:
            return ToolResult(
                ok=False,
                content="Wait cancelled and the process tree was terminated.",
                data=snapshot.to_dict(),
            )
        prefix = (
            "Process is still running after the wait window."
            if snapshot.running
            else "Process exited."
        )
        return _result_from_snapshot(snapshot, content_prefix=prefix)

    return AgentTool(
        name="exec_wait",
        description=(
            "Wait for an existing managed process, streaming newly observed terminal/stdout output. "
            "A wait_timeout_seconds window only stops this wait call; the process keeps its original "
            "lifetime timeout."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "process_id": {"type": "string"},
                "wait_timeout_seconds": {"type": "number"},
            },
            "required": ["process_id"],
            "additionalProperties": False,
        },
        handler=wait_process,
        effect=ToolEffect.READ_ONLY,
    )


def exec_write_tool() -> AgentTool:
    def write(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        managed = _store(context).get(
            str(arguments["process_id"]),
            session_id=context.session_id,
        )
        has_text = "text" in arguments
        has_control = "control" in arguments
        eof = bool(arguments.get("eof", False))
        if sum((has_text, has_control, eof)) != 1:
            raise ValueError("provide exactly one of text, control, or eof=true")
        if has_text:
            text = str(arguments.get("text") or "")
            managed.write_stdin(text)
            content = f"Wrote {len(text)} characters to {managed.process_id}."
        elif has_control:
            control = str(arguments.get("control") or "")
            managed.send_control(control)
            content = f"Sent Ctrl+{control.upper()} to {managed.process_id}."
        else:
            managed.close_stdin()
            content = f"Sent EOF to {managed.process_id}."
        snapshot = managed.snapshot(drain_delta=True)
        _emit_snapshot_delta(context, managed, snapshot)
        _emit_exit_once(context, managed, snapshot)
        return ToolResult(ok=True, content=content, data=snapshot.to_dict())

    return AgentTool(
        name="exec_write",
        description=(
            "Write text, one terminal control character, or EOF to an existing managed process."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "process_id": {"type": "string"},
                "text": {"type": "string"},
                "control": {"type": "string"},
                "eof": {"type": "boolean"},
            },
            "required": ["process_id"],
            "additionalProperties": False,
        },
        handler=write,
        effect=ToolEffect.SENSITIVE,
    )


def exec_resize_tool() -> AgentTool:
    def resize(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        managed = _store(context).get(
            str(arguments["process_id"]),
            session_id=context.session_id,
        )
        rows, cols = validate_terminal_size(arguments["rows"], arguments["cols"])
        snapshot = managed.resize(rows=rows, cols=cols)
        _emit_snapshot_delta(context, managed, snapshot)
        return ToolResult(
            ok=True,
            content=f"Resized {managed.process_id} PTY to {rows}x{cols}.",
            data=snapshot.to_dict(),
        )

    return AgentTool(
        name="exec_resize",
        description="Resize a real PTY. Fails for pipe-backed processes; resize is never a no-op.",
        input_schema={
            "type": "object",
            "properties": {
                "process_id": {"type": "string"},
                "rows": {"type": "integer"},
                "cols": {"type": "integer"},
            },
            "required": ["process_id", "rows", "cols"],
            "additionalProperties": False,
        },
        handler=resize,
        effect=ToolEffect.SENSITIVE,
    )


def exec_interrupt_tool() -> AgentTool:
    def interrupt(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        managed = _store(context).get(
            str(arguments["process_id"]),
            session_id=context.session_id,
        )
        managed.interrupt()
        snapshot = managed.snapshot(drain_delta=True)
        _emit_snapshot_delta(context, managed, snapshot)
        _emit_exit_once(context, managed, snapshot)
        return ToolResult(
            ok=True,
            content=f"Sent interrupt to {managed.process_id}.",
            data=snapshot.to_dict(),
        )

    return AgentTool(
        name="exec_interrupt",
        description=(
            "Send Ctrl+C/SIGINT semantics to an existing process without forcing tree termination."
        ),
        input_schema={
            "type": "object",
            "properties": {"process_id": {"type": "string"}},
            "required": ["process_id"],
            "additionalProperties": False,
        },
        handler=interrupt,
        effect=ToolEffect.SENSITIVE,
    )


def exec_terminate_tool() -> AgentTool:
    def terminate(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        managed = _store(context).get(
            str(arguments["process_id"]),
            session_id=context.session_id,
        )
        was_running = managed.running
        managed.terminate_tree()
        snapshot = managed.wait(
            on_output=lambda stdout, stderr: _emit_output(context, managed, stdout, stderr)
        )
        _emit_exit_once(context, managed, snapshot)
        content = (
            f"Terminated process tree {managed.process_id}."
            if was_running
            else f"Process {managed.process_id} was already exited; terminate is idempotent."
        )
        return ToolResult(ok=True, content=content, data=snapshot.to_dict())

    return AgentTool(
        name="exec_terminate",
        description="Idempotently terminate the root process and its descendant process tree.",
        input_schema={
            "type": "object",
            "properties": {"process_id": {"type": "string"}},
            "required": ["process_id"],
            "additionalProperties": False,
        },
        handler=terminate,
        effect=ToolEffect.SENSITIVE,
    )


def workspace_command_tool() -> AgentTool:
    return AgentTool(
        name="run_workspace_command",
        description="Compatibility alias for exec with wait=true.",
        input_schema=_exec_schema(include_wait=False),
        handler=lambda context, arguments: _exec_handler(
            context,
            arguments,
            force_wait=True,
        ),
        effect=ToolEffect.SENSITIVE,
    )


def start_workspace_command_tool() -> AgentTool:
    return AgentTool(
        name="start_workspace_command",
        description="Compatibility alias for exec with wait=false.",
        input_schema=_exec_schema(include_wait=False),
        handler=lambda context, arguments: _exec_handler(
            context,
            arguments,
            force_wait=False,
        ),
        effect=ToolEffect.SENSITIVE,
    )


def poll_workspace_process_tool() -> AgentTool:
    def poll(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        managed = _store(context).get(
            str(arguments["process_id"]),
            session_id=context.session_id,
        )
        snapshot = managed.snapshot(drain_delta=True)
        _emit_snapshot_delta(context, managed, snapshot)
        _emit_exit_once(context, managed, snapshot)
        content = f"process={snapshot.process_id} status={snapshot.state.value}"
        if snapshot.stdout_delta:
            label = "terminal delta" if snapshot.pty else "stdout delta"
            content += f"\n{label}:\n{snapshot.stdout_delta}"
        if snapshot.stderr_delta:
            content += f"\nstderr delta:\n{snapshot.stderr_delta}"
        if snapshot.returncode is not None:
            content += f"\nexit={snapshot.returncode}"
        return ToolResult(ok=True, content=content, data=snapshot.to_dict())

    return AgentTool(
        name="poll_workspace_process",
        description="Compatibility non-blocking snapshot/drain for an existing managed process.",
        input_schema={
            "type": "object",
            "properties": {"process_id": {"type": "string"}},
            "required": ["process_id"],
            "additionalProperties": False,
        },
        handler=poll,
        effect=ToolEffect.READ_ONLY,
    )


def list_workspace_processes_tool() -> AgentTool:
    def list_processes(context: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        snapshots = _store(context).list_for_session(context.session_id)
        rows = [snapshot.to_dict() for snapshot in snapshots]
        return ToolResult(
            ok=True,
            content=f"{len(rows)} managed processes for this Loom session.",
            data={"processes": rows},
        )

    return AgentTool(
        name="list_workspace_processes",
        description="List managed command processes owned by the current Loom session.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=list_processes,
        effect=ToolEffect.READ_ONLY,
    )


def write_workspace_process_tool() -> AgentTool:
    unified = exec_write_tool()
    return AgentTool(
        name="write_workspace_process",
        description="Compatibility alias for exec_write text input.",
        input_schema={
            "type": "object",
            "properties": {
                "process_id": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["process_id", "text"],
            "additionalProperties": False,
        },
        handler=unified.handler,
        effect=ToolEffect.SENSITIVE,
    )


def interrupt_workspace_process_tool() -> AgentTool:
    unified = exec_interrupt_tool()
    return AgentTool(
        name="interrupt_workspace_process",
        description="Compatibility alias for exec_interrupt.",
        input_schema=unified.input_schema,
        handler=unified.handler,
        effect=ToolEffect.SENSITIVE,
    )


def terminate_workspace_process_tool() -> AgentTool:
    unified = exec_terminate_tool()
    return AgentTool(
        name="terminate_workspace_process",
        description="Compatibility alias for exec_terminate.",
        input_schema=unified.input_schema,
        handler=unified.handler,
        effect=ToolEffect.SENSITIVE,
    )


def managed_process_tools() -> tuple[AgentTool, ...]:
    return (
        exec_tool(),
        exec_wait_tool(),
        exec_write_tool(),
        exec_resize_tool(),
        exec_interrupt_tool(),
        exec_terminate_tool(),
        workspace_command_tool(),
        start_workspace_command_tool(),
        poll_workspace_process_tool(),
        list_workspace_processes_tool(),
        write_workspace_process_tool(),
        interrupt_workspace_process_tool(),
        terminate_workspace_process_tool(),
    )


__all__ = [
    "exec_interrupt_tool",
    "exec_resize_tool",
    "exec_terminate_tool",
    "exec_tool",
    "exec_wait_tool",
    "exec_write_tool",
    "interrupt_workspace_process_tool",
    "list_workspace_processes_tool",
    "managed_process_tools",
    "poll_workspace_process_tool",
    "start_workspace_command_tool",
    "terminate_workspace_process_tool",
    "workspace_command_tool",
    "write_workspace_process_tool",
]
