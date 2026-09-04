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
    PermissionMode,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import (
    AGENT_FAST_ROLE,
    AIConfiguration,
    CredentialRef,
    CredentialResolver,
    ModelBinding,
    ProviderAdapter,
    ProviderConnection,
    build_ai_platform,
)


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


def _build_runtime(args: argparse.Namespace) -> tuple[AgentRuntime, FileAgentSessionStore, str]:
    connection, model, secret = _resolve_connection(args)
    binding = ModelBinding(
        role_id=AGENT_FAST_ROLE.role_id,
        provider_id=connection.provider_id,
        model=model,
        capabilities=AGENT_FAST_ROLE.required_capabilities,
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


def _run_prompt(runtime: AgentRuntime, session_id: str, prompt: str):
    try:
        result = runtime.start_turn(session_id, prompt)
        result = _finish_result(runtime, result)
    except KeyboardInterrupt:
        print("\nStopping current turn…", file=sys.stderr)
        result = runtime.cancel(session_id)
    if result.final_text:
        print(f"\nLoom> {result.final_text}")
    if result.status not in {AgentStatus.COMPLETED, AgentStatus.CANCELLED}:
        detail = result.error or result.status.value
        print(f"\n[{result.status.value}] {detail}", file=sys.stderr)
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


def _print_help() -> None:
    print(
        "Commands:\n"
        "  /new [path]          create a fresh session, reusing current workspace unless path is given\n"
        "  /sessions            list saved sessions\n"
        "  /use <session-id>    switch to a saved session\n"
        "  /session             show current session id\n"
        "  /workspace           show current workspace path\n"
        "  /permissions         show current permission mode\n"
        "  /permissions <mode>  set read-only / approval / workspace / full-access\n"
        "  /usage               show current token usage\n"
        "  /help                show this help\n"
        "  /quit                exit Loom\n"
        "Ctrl+C stops the active turn; Ctrl+D exits the prompt."
    )


def _interactive(runtime: AgentRuntime, store: FileAgentSessionStore, session_id: str) -> int:
    session = runtime.get_session(session_id)
    print("Loom interactive agent")
    print(f"Session:     {session_id}")
    print(f"Workspace:   {session.workspace_dir}")
    print(f"Permissions: {session.permission_mode.value}")
    print("Type /help for commands.\n")
    while True:
        try:
            text = input("You> ").strip()
        except EOFError:
            print()
            return 0
        except KeyboardInterrupt:
            print()
            continue
        if not text:
            continue
        if text in {"/quit", "/exit"}:
            return 0
        if text == "/help":
            _print_help()
            continue
        if text == "/new" or text.startswith("/new "):
            supplied = text[4:].strip()
            current = runtime.get_session(session_id)
            workspace = supplied or current.workspace_dir
            try:
                session = _create_workspace_session(
                    runtime,
                    workspace,
                    current.permission_mode,
                )
            except (SystemExit, ValueError) as exc:
                print(str(exc), file=sys.stderr)
                continue
            session_id = session.session_id
            print(f"New session: {session_id}")
            print(f"Workspace:   {session.workspace_dir}")
            print(f"Permissions: {session.permission_mode.value}")
            continue
        if text == "/sessions":
            _list_sessions(store)
            continue
        if text.startswith("/use "):
            candidate = text[5:].strip()
            try:
                session = runtime.get_session(candidate)
                if session.status is AgentStatus.RUNNING:
                    runtime.recover_interrupted(candidate)
                    session = runtime.get_session(candidate)
                session_id = candidate
                print(f"Using session: {session_id}")
                print(f"Workspace:     {session.workspace_dir}")
                print(f"Permissions:   {session.permission_mode.value}")
            except Exception as exc:
                print(f"Cannot load session: {exc}", file=sys.stderr)
            continue
        if text == "/session":
            print(session_id)
            continue
        if text == "/workspace":
            print(runtime.get_session(session_id).workspace_dir)
            continue
        if text == "/permissions":
            print(runtime.get_session(session_id).permission_mode.value)
            continue
        if text.startswith("/permissions "):
            value = text[len("/permissions "):].strip()
            try:
                session = runtime.set_permission_mode(session_id, PermissionMode(value))
                print(f"Permissions: {session.permission_mode.value}")
            except (ValueError, RuntimeError) as exc:
                print(f"Cannot change permissions: {exc}", file=sys.stderr)
            continue
        if text == "/usage":
            usage = runtime.get_session(session_id).usage
            print(f"input={usage.input_tokens} output={usage.output_tokens} total={usage.total_tokens}")
            continue
        if text.startswith("/"):
            print("Unknown command. Type /help.", file=sys.stderr)
            continue
        _run_prompt(runtime, session_id, text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Loom — personal general-purpose tool-using AI agent")
    parser.add_argument("prompt", nargs="*", help="run one prompt non-interactively")
    parser.add_argument("--provider", choices=["openai", "openai-compatible"])
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--home", help="runtime state root; defaults to ~/.loom")
    parser.add_argument(
        "--workspace",
        help="workspace for a new session; defaults to the current directory",
    )
    parser.add_argument("--session", help="resume an existing Loom session")
    parser.add_argument(
        "--permission-mode",
        choices=[mode.value for mode in PermissionMode],
        help="permission preset for a new session, or explicit override when resuming",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--quiet-events", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.session and args.workspace:
        raise SystemExit("--workspace cannot be combined with --session; resumed sessions keep their saved workspace.")

    runtime, store, model = _build_runtime(args)
    if not args.quiet_events:
        runtime.subscribe(_event_printer)

    if args.session:
        session = runtime.get_session(args.session)
        if session.status is AgentStatus.RUNNING:
            runtime.recover_interrupted(args.session)
            session = runtime.get_session(args.session)
        if args.permission_mode:
            session = runtime.set_permission_mode(args.session, PermissionMode(args.permission_mode))
        session_id = args.session
    else:
        session = _create_workspace_session(
            runtime,
            args.workspace or Path.cwd(),
            _resolve_new_permission_mode(args),
        )
        session_id = session.session_id

    session = runtime.get_session(session_id)
    print(f"Loom · {model} · session {session_id}")
    print(f"Workspace · {session.workspace_dir}")
    print(f"Permissions · {session.permission_mode.value}")
    if args.prompt:
        result = _run_prompt(runtime, session_id, " ".join(args.prompt))
        return 0 if result.status is AgentStatus.COMPLETED else 1
    return _interactive(runtime, store, session_id)


if __name__ == "__main__":
    raise SystemExit(main())
