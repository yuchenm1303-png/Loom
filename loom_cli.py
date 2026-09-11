from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from app.agent_runtime import (
    AgentEvent,
    AgentEventKind,
    AgentRuntime,
    AgentStatus,
    FileAgentSessionStore,
    GoalStatus,
    PermissionMode,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import (
    AGENT_FAST_ROLE,
    AIConfiguration,
    CredentialRef,
    CredentialResolver,
    ModelBinding,
    ModelCapability,
    ProviderAdapter,
    ProviderConnection,
    build_ai_platform,
)
from app.ai.model_context import model_context_limits_from_env


_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_RUNTIME_KEY_ALIAS = "loom-api-key"


def _first_env(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def _resolve_connection(args: argparse.Namespace) -> tuple[ProviderConnection, str, str]:
    provider_text = str(args.provider or _first_env("LOOM_PROVIDER")).strip().casefold()
    requested_base = str(args.base_url or _first_env("LOOM_BASE_URL")).strip()

    if not provider_text:
        if requested_base or _first_env("DASHSCOPE_API_KEY", "AI_API_KEY"):
            provider_text = ProviderAdapter.OPENAI_COMPATIBLE.value
        elif _first_env("OPENAI_API_KEY"):
            provider_text = ProviderAdapter.OPENAI.value
        else:
            provider_text = ProviderAdapter.OPENAI_COMPATIBLE.value

    try:
        adapter = ProviderAdapter(provider_text)
    except ValueError as exc:
        raise SystemExit(f"Unsupported provider adapter: {provider_text}") from exc

    if adapter not in {ProviderAdapter.OPENAI, ProviderAdapter.OPENAI_COMPATIBLE}:
        raise SystemExit(f"Provider adapter is not executable yet: {adapter.value}")

    if adapter is ProviderAdapter.OPENAI:
        base_url = ""
        secret = _first_env("LOOM_API_KEY", "OPENAI_API_KEY")
    else:
        base_url = requested_base
        if not base_url and _first_env("DASHSCOPE_API_KEY", "AI_API_KEY"):
            base_url = _DASHSCOPE_BASE_URL
        if not base_url:
            raise SystemExit(
                "OpenAI-compatible mode requires --base-url or LOOM_BASE_URL. "
                "DashScope users can set DASHSCOPE_API_KEY / AI_API_KEY and use the default endpoint."
            )
        secret = _first_env("LOOM_API_KEY", "AI_API_KEY", "DASHSCOPE_API_KEY", "OPENAI_API_KEY")

    if not secret:
        raise SystemExit(
            "No API key found. Set LOOM_API_KEY, or the provider-specific OPENAI_API_KEY / DASHSCOPE_API_KEY."
        )

    model = str(args.model or _first_env("LOOM_MODEL", "AGENT_MODEL")).strip()
    if not model and "dashscope.aliyuncs.com" in base_url:
        model = "qwen-plus"
    if not model:
        raise SystemExit("No model selected. Pass --model or set LOOM_MODEL.")

    connection = ProviderConnection(
        provider_id="loom-primary",
        adapter=adapter,
        credential_ref=CredentialRef.runtime(_RUNTIME_KEY_ALIAS),
        base_url=base_url,
        display_name="Loom Primary",
    )
    return connection, model, secret


def _vision_enabled(args: argparse.Namespace) -> bool:
    """Whether this launch declares the bound model able to read images.

    Capability here is a claim about the endpoint, not about Loom. Only the
    caller who chose the model knows the answer, so a launcher that never asks
    gets the permissive default: attaching an image is an explicit user action,
    and no image is sent unless one is attached.
    """
    return bool(getattr(args, "vision", True))


def _build_runtime(args: argparse.Namespace) -> tuple[AgentRuntime, FileAgentSessionStore, str]:
    connection, model, secret = _resolve_connection(args)
    capabilities = set(AGENT_FAST_ROLE.required_capabilities)
    if _vision_enabled(args):
        capabilities.add(ModelCapability.VISION)
    binding = ModelBinding(
        role_id=AGENT_FAST_ROLE.role_id,
        provider_id=connection.provider_id,
        model=model,
        capabilities=frozenset(capabilities),
        context_limits=model_context_limits_from_env(),
    )
    configuration = AIConfiguration.build(
        roles=(AGENT_FAST_ROLE,),
        providers=(connection,),
        bindings=(binding,),
    )
    resolver = CredentialResolver(
        runtime_lookup=lambda alias: secret if alias == _RUNTIME_KEY_ALIAS else None
    )
    platform = build_ai_platform(
        configuration,
        credential_resolver=resolver,
        request_timeout_seconds=float(args.timeout),
    )
    home = Path(args.home or _first_env("LOOM_HOME") or (Path.home() / ".loom")).expanduser().resolve()
    store = FileAgentSessionStore(home)
    runtime = AgentRuntime(platform=platform, store=store, tools=loom_default_tools())
    return runtime, store, model


def _resolve_workspace(value: str | Path) -> Path:
    workspace = Path(value).expanduser().resolve()
    if not workspace.exists():
        raise SystemExit(f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise SystemExit(f"Workspace is not a directory: {workspace}")
    return workspace


def _resolve_new_permission_mode(args: argparse.Namespace) -> PermissionMode:
    raw = str(args.permission_mode or _first_env("LOOM_PERMISSION_MODE") or PermissionMode.APPROVAL.value)
    try:
        return PermissionMode(raw)
    except ValueError as exc:
        choices = ", ".join(mode.value for mode in PermissionMode)
        raise SystemExit(f"Invalid permission mode {raw!r}; choose one of: {choices}") from exc


def _create_workspace_session(
    runtime: AgentRuntime,
    workspace: str | Path,
    permission_mode: PermissionMode | str,
):
    root = _resolve_workspace(workspace)
    return runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=root,
        permission_mode=permission_mode,
    )


def _event_printer(event: AgentEvent) -> None:
    data = event.data
    if event.kind is AgentEventKind.MODEL_REQUESTED:
        print(f"  · model step {data.get('step', '?')}", flush=True)
    elif event.kind is AgentEventKind.TOOL_REQUESTED:
        arguments = json.dumps(data.get("arguments") or {}, ensure_ascii=False)
        print(f"  → {data.get('tool', '')} {arguments}", flush=True)
    elif event.kind is AgentEventKind.TOOL_STARTED:
        print(f"  · running {data.get('tool', '')}", flush=True)
    elif event.kind is AgentEventKind.TOOL_COMPLETED:
        print(f"  ✓ {data.get('tool', '')}: {str(data.get('content') or '')[:240]}", flush=True)
    elif event.kind is AgentEventKind.TOOL_FAILED:
        print(f"  ✗ {data.get('tool', '')}: {str(data.get('content') or '')[:240]}", flush=True)
    elif event.kind is AgentEventKind.TOOL_DENIED and data.get("source") == "permission":
        print(f"  ⛔ {data.get('tool', '')}: blocked by permissions", flush=True)
    elif event.kind is AgentEventKind.QUEUE_DISPATCHED:
        print(f"  ↪ queued turn {data.get('queue_id', '')}", flush=True)
    elif event.kind is AgentEventKind.HISTORY_REPAIRED:
        print("  ↻ repaired interrupted tool history", flush=True)
    elif event.kind is AgentEventKind.MEMORY_EXTRACTED:
        print(f"  ◇ memory extracted: {data.get('candidate_count', 0)} candidate(s)", flush=True)
    elif event.kind is AgentEventKind.MEMORY_CONSOLIDATED:
        print(f"  ◇ memory consolidated: {data.get('count', 0)} record(s)", flush=True)
    elif event.kind is AgentEventKind.MEMORY_FORGOTTEN:
        print(f"  ◇ memory forgotten: {data.get('memory_id', '')}", flush=True)


def _finish_result(runtime: AgentRuntime, result):
    while result.status is AgentStatus.WAITING_APPROVAL and result.pending_approval is not None:
        pending = result.pending_approval
        print("\nApproval required")
        print(f"  tool:   {pending.tool_name}")
        print(f"  effect: {pending.effect.value}")
        print(f"  reason: {pending.reason}")
        print(json.dumps(pending.arguments, ensure_ascii=False, indent=2))
        answer = input("Approve this tool call? [y/N] ").strip().casefold()
        result = runtime.resume_approval(
            result.session_id,
            pending.call_id,
            approved=answer in {"y", "yes"},
        )
    return result


def _print_run_result(result) -> None:
    if result.final_text:
        print(f"\nLoom> {result.final_text}")
    if result.status not in {AgentStatus.COMPLETED, AgentStatus.CANCELLED}:
        detail = result.error or result.status.value
        print(f"\n[{result.status.value}] {detail}", file=sys.stderr)


def _run_prompt(runtime: AgentRuntime, session_id: str, prompt: str):
    try:
        result = runtime.start_turn(session_id, prompt)
        result = _finish_result(runtime, result)
    except KeyboardInterrupt:
        print("\nStopping current turn…", file=sys.stderr)
        result = runtime.cancel(session_id)
    _print_run_result(result)
    return result


def _list_sessions(store: FileAgentSessionStore) -> None:
    rows = []
    if store.root.is_dir():
        for directory in store.root.iterdir():
            if not (directory / "session.json").is_file():
                continue
            try:
                session = store.load(directory.name)
            except Exception:
                continue
            rows.append(session)
    rows.sort(key=lambda item: item.updated_at, reverse=True)
    if not rows:
        print("No saved sessions.")
        return
    for session in rows[:30]:
        print(
            f"{session.session_id}  {session.status.value:16}  "
            f"permissions={session.permission_mode.value:11}  "
            f"tokens={session.usage.total_tokens:<8}  {session.updated_at}  {session.workspace_dir}"
        )


def _show_goal(runtime: AgentRuntime, session_id: str) -> None:
    goal = runtime.get_goal(session_id)
    if goal is None:
        print("No durable goal.")
        return
    print(json.dumps(goal.to_dict(), ensure_ascii=False, indent=2))


def _set_goal(runtime: AgentRuntime, session_id: str, objective: str, token_budget: int | None) -> None:
    goal = runtime.set_goal(session_id, objective, token_budget=token_budget)
    print(json.dumps(goal.to_dict(), ensure_ascii=False, indent=2))


def _clear_goal(runtime: AgentRuntime, session_id: str) -> None:
    runtime.clear_goal(session_id)
    print("Goal cleared.")


def _list_queue(runtime: AgentRuntime, session_id: str) -> None:
    rows = runtime.list_queued_turns(session_id)
    if not rows:
        print("Queue is empty.")
        return
    for item in rows:
        print(json.dumps(item.to_dict(), ensure_ascii=False))


def _enqueue_turn(runtime: AgentRuntime, session_id: str, text: str) -> None:
    item = runtime.enqueue_turn(session_id, text)
    print(json.dumps(item.to_dict(), ensure_ascii=False, indent=2))


def _compact(runtime: AgentRuntime, session_id: str, summary: str, keep_recent: int) -> None:
    checkpoint = runtime.compact_context(session_id, summary, keep_recent=keep_recent)
    print(json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2))


def _extract_memory(runtime: AgentRuntime, session_id: str) -> None:
    result = runtime.extract_memory_from_thread(session_id)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


def _forget_memory(runtime: AgentRuntime, memory_id: str, workspace: str | None) -> None:
    runtime.forget_memory(memory_id, workspace=workspace)
    print(f"Forgot memory {memory_id}.")


def _memory_status(runtime: AgentRuntime, session_id: str) -> None:
    print(json.dumps(runtime.memory_status(session_id), ensure_ascii=False, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Loom Agent runtime")
    parser.add_argument("prompt", nargs="?", help="user prompt")
    parser.add_argument("--session", help="resume an existing session")
    parser.add_argument("--workspace", help="agent workspace root")
    parser.add_argument("--home", help="runtime state root; defaults to ~/.loom")
    parser.add_argument("--provider", choices=["openai", "openai-compatible"])
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--vision",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="declare that the selected model can read attached images",
    )
    parser.add_argument(
        "--permission-mode",
        choices=[mode.value for mode in PermissionMode],
        help="default permission mode for new sessions",
    )
    parser.add_argument("--goal", help="set/replace durable thread objective before running")
    parser.add_argument("--goal-token-budget", type=int)
    parser.add_argument("--show-goal", action="store_true")
    parser.add_argument("--clear-goal", action="store_true")
    parser.add_argument("--enqueue", help="queue a turn for later dispatch")
    parser.add_argument("--show-queue", action="store_true")
    parser.add_argument("--compact-summary", help="replace older history with a durable summary")
    parser.add_argument("--compact-keep-recent", type=int, default=8)
    parser.add_argument("--extract-memory", action="store_true")
    parser.add_argument("--memory-status", action="store_true")
    parser.add_argument("--forget-memory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtime, store, _model = _build_runtime(args)
    runtime.subscribe(_event_printer)
    try:
        if args.session:
            session = runtime.get_session(args.session)
        else:
            workspace = _resolve_workspace(args.workspace or Path.cwd())
            session = runtime.create_session(
                AGENT_FAST_ROLE.role_id,
                workspace_dir=workspace,
                permission_mode=_resolve_new_permission_mode(args),
            )

        if args.goal:
            _set_goal(runtime, session.session_id, args.goal, args.goal_token_budget)
        if args.clear_goal:
            _clear_goal(runtime, session.session_id)
        if args.show_goal:
            _show_goal(runtime, session.session_id)
        if args.enqueue:
            _enqueue_turn(runtime, session.session_id, args.enqueue)
        if args.show_queue:
            _list_queue(runtime, session.session_id)
        if args.compact_summary:
            _compact(runtime, session.session_id, args.compact_summary, args.compact_keep_recent)
        if args.extract_memory:
            _extract_memory(runtime, session.session_id)
        if args.memory_status:
            _memory_status(runtime, session.session_id)
        if args.forget_memory:
            _forget_memory(runtime, args.forget_memory, session.workspace_dir)
        if args.prompt:
            _run_prompt(runtime, session.session_id, args.prompt)
        elif not any(
            [
                args.show_goal,
                args.clear_goal,
                args.enqueue,
                args.show_queue,
                args.compact_summary,
                args.extract_memory,
                args.memory_status,
                args.forget_memory,
            ]
        ):
            print(session.session_id)
        return 0
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
