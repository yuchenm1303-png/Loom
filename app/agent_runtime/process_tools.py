from __future__ import annotations

from typing import Any

from .contracts import AgentEventKind, ToolEffect
from .process_runtime import ProcessSnapshot, ProcessStore, validate_argv, validate_timeout
from .tools import AgentTool, ToolContext, ToolResult


def _store(context: ToolContext) -> ProcessStore:
    store = context.service("process_store")
    if not isinstance(store, ProcessStore):
        raise RuntimeError("process runtime service is unavailable")
    return store


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
    return argv, relative_cwd, cwd, timeout_seconds, stdin_text


def _result_from_snapshot(snapshot: ProcessSnapshot, *, content_prefix: str = "") -> ToolResult:
    parts = []
    if content_prefix:
        parts.append(content_prefix)
    parts.append(
        f"process={snapshot.process_id} status={'running' if snapshot.running else 'exited'}"
    )
    if snapshot.sandbox.enforced:
        parts.append(
            f"sandbox={snapshot.sandbox.backend.value}:{snapshot.sandbox.mode.value}"
        )
    elif snapshot.sandbox.mode.value != "disabled":
        parts.append(f"sandbox=not-enforced ({snapshot.sandbox.reason})")
    if snapshot.returncode is not None:
        parts.append(f"exit={snapshot.returncode}")
    if snapshot.stdout:
        parts.append(f"stdout:\n{snapshot.stdout}")
    if snapshot.stderr:
        parts.append(f"stderr:\n{snapshot.stderr}")
    ok = snapshot.running or snapshot.returncode == 0
    if snapshot.timed_out:
        ok = False
        parts.insert(0, "Command timed out.")
    return ToolResult(ok=ok, content="\n".join(parts), data=snapshot.to_dict())


def workspace_command_tool() -> AgentTool:
    def run_command(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        argv, relative_cwd, cwd, timeout_seconds, stdin_text = _resolve_command(context, arguments)
        store = _store(context)

        started = store.start(
            session_id=context.session_id,
            argv=argv,
            cwd=cwd,
            workspace=context.workspace,
            permission_mode=context.permission_mode,
            timeout_seconds=timeout_seconds,
            stdin_text=stdin_text,
        )
        # Foreground execution mirrors subprocess.run(input=...): once the
        # supplied input has been written, close stdin so programs waiting for
        # EOF can finish. Background processes intentionally keep stdin open.
        started.close_stdin()
        context.emit(
            AgentEventKind.PROCESS_STARTED,
            {
                "process_id": started.process_id,
                "argv": list(argv),
                "cwd": relative_cwd,
                "background": False,
                "sandbox": started.sandbox.to_dict(),
            },
        )

        def emit_output(stdout: str, stderr: str) -> None:
            context.emit(
                AgentEventKind.PROCESS_OUTPUT,
                {
                    "process_id": started.process_id,
                    "stdout": stdout,
                    "stderr": stderr,
                },
            )

        snapshot = started.wait(
            cancel_check=lambda: context.cancelled,
            on_output=emit_output,
        )
        context.emit(
            AgentEventKind.PROCESS_EXITED,
            {
                "process_id": snapshot.process_id,
                "returncode": snapshot.returncode,
                "timed_out": snapshot.timed_out,
                "sandbox": snapshot.sandbox.to_dict(),
            },
        )
        if context.cancelled:
            return ToolResult(
                ok=False,
                content="Command cancelled and its process tree was terminated.",
                data=snapshot.to_dict(),
            )
        return _result_from_snapshot(snapshot)

    return AgentTool(
        name="run_workspace_command",
        description=(
            "Run one executable directly with argv and wait for it to exit. The command gets a managed "
            "process id, bounded output, timeout enforcement, cancellation-aware process-tree termination, "
            "and secret-like environment variables are stripped. Loom attempts the configured OS sandbox "
            "for non-full-access sessions and reports whether isolation was actually enforced. No shell expansion is used."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string"},
                "stdin": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["argv"],
            "additionalProperties": False,
        },
        handler=run_command,
        effect=ToolEffect.SENSITIVE,
    )


def start_workspace_command_tool() -> AgentTool:
    def start_command(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        argv, relative_cwd, cwd, timeout_seconds, stdin_text = _resolve_command(context, arguments)
        managed = _store(context).start(
            session_id=context.session_id,
            argv=argv,
            cwd=cwd,
            workspace=context.workspace,
            permission_mode=context.permission_mode,
            timeout_seconds=timeout_seconds,
            stdin_text=stdin_text,
        )
        snapshot = managed.snapshot(drain_delta=True)
        context.emit(
            AgentEventKind.PROCESS_STARTED,
            {
                "process_id": managed.process_id,
                "argv": list(argv),
                "cwd": relative_cwd,
                "background": True,
                "sandbox": managed.sandbox.to_dict(),
            },
        )
        return _result_from_snapshot(
            snapshot,
            content_prefix="Started managed background process.",
        )

    return AgentTool(
        name="start_workspace_command",
        description=(
            "Start a long-running executable directly in the workspace and return immediately with a process_id. "
            "Use poll_workspace_process, write_workspace_process, interrupt_workspace_process or "
            "terminate_workspace_process to interact with it later. Sandbox enforcement is reported in the result."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string"},
                "stdin": {"type": "string"},
                "timeout_seconds": {"type": "integer"},
            },
            "required": ["argv"],
            "additionalProperties": False,
        },
        handler=start_command,
        effect=ToolEffect.SENSITIVE,
    )


def poll_workspace_process_tool() -> AgentTool:
    def poll(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        managed = _store(context).get(str(arguments["process_id"]), session_id=context.session_id)
        snapshot = managed.snapshot(drain_delta=True)
        content = (
            f"process={snapshot.process_id} status={'running' if snapshot.running else 'exited'}"
        )
        if snapshot.stdout_delta:
            content += f"\nstdout delta:\n{snapshot.stdout_delta}"
        if snapshot.stderr_delta:
            content += f"\nstderr delta:\n{snapshot.stderr_delta}"
        if not snapshot.running:
            content += f"\nexit={snapshot.returncode}"
            context.emit(
                AgentEventKind.PROCESS_EXITED,
                {
                    "process_id": snapshot.process_id,
                    "returncode": snapshot.returncode,
                    "timed_out": snapshot.timed_out,
                    "sandbox": snapshot.sandbox.to_dict(),
                },
            )
        return ToolResult(ok=True, content=content, data=snapshot.to_dict())

    return AgentTool(
        name="poll_workspace_process",
        description="Poll one managed process and drain new stdout/stderr since the last poll.",
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
    def write(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        managed = _store(context).get(str(arguments["process_id"]), session_id=context.session_id)
        text = str(arguments["text"])
        managed.write_stdin(text)
        snapshot = managed.snapshot(drain_delta=True)
        return ToolResult(
            ok=True,
            content=f"Wrote {len(text)} characters to {managed.process_id} stdin.",
            data=snapshot.to_dict(),
        )

    return AgentTool(
        name="write_workspace_process",
        description="Write text to stdin of a running managed workspace process.",
        input_schema={
            "type": "object",
            "properties": {
                "process_id": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["process_id", "text"],
            "additionalProperties": False,
        },
        handler=write,
        effect=ToolEffect.SENSITIVE,
    )


def interrupt_workspace_process_tool() -> AgentTool:
    def interrupt(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        managed = _store(context).get(str(arguments["process_id"]), session_id=context.session_id)
        managed.interrupt()
        snapshot = managed.snapshot(drain_delta=True)
        return ToolResult(
            ok=True,
            content=f"Sent interrupt to {managed.process_id}.",
            data=snapshot.to_dict(),
        )

    return AgentTool(
        name="interrupt_workspace_process",
        description="Send an interrupt signal to a running managed workspace process.",
        input_schema={
            "type": "object",
            "properties": {"process_id": {"type": "string"}},
            "required": ["process_id"],
            "additionalProperties": False,
        },
        handler=interrupt,
        effect=ToolEffect.SENSITIVE,
    )


def terminate_workspace_process_tool() -> AgentTool:
    def terminate(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        managed = _store(context).get(str(arguments["process_id"]), session_id=context.session_id)
        managed.terminate_tree()
        snapshot = managed.wait()
        context.emit(
            AgentEventKind.PROCESS_EXITED,
            {
                "process_id": snapshot.process_id,
                "returncode": snapshot.returncode,
                "timed_out": snapshot.timed_out,
                "sandbox": snapshot.sandbox.to_dict(),
            },
        )
        return ToolResult(
            ok=True,
            content=f"Terminated process tree {managed.process_id}.",
            data=snapshot.to_dict(),
        )

    return AgentTool(
        name="terminate_workspace_process",
        description="Terminate a managed workspace process and its descendant process tree.",
        input_schema={
            "type": "object",
            "properties": {"process_id": {"type": "string"}},
            "required": ["process_id"],
            "additionalProperties": False,
        },
        handler=terminate,
        effect=ToolEffect.SENSITIVE,
    )


def managed_process_tools() -> tuple[AgentTool, ...]:
    return (
        workspace_command_tool(),
        start_workspace_command_tool(),
        poll_workspace_process_tool(),
        list_workspace_processes_tool(),
        write_workspace_process_tool(),
        interrupt_workspace_process_tool(),
        terminate_workspace_process_tool(),
    )


__all__ = [
    "interrupt_workspace_process_tool",
    "list_workspace_processes_tool",
    "managed_process_tools",
    "poll_workspace_process_tool",
    "start_workspace_command_tool",
    "terminate_workspace_process_tool",
    "workspace_command_tool",
    "write_workspace_process_tool",
]
