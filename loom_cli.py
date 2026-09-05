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
    budget = "unlimited" if goal.token_budget is None else str(goal.token_budget)
    print(f"Goal:    {goal.objective}")
    print(f"Status:  {goal.status.value}")
    print(f"Tokens:  {goal.tokens_used}/{budget}")


def _show_queue(runtime: AgentRuntime, session_id: str) -> None:
    items = runtime.list_queued_turns(session_id)
    if not items:
        print("Queue is empty.")
        return
    for index, item in enumerate(items, start=1):
        preview = item.text.replace("\n", " ")[:100]
        print(f"{index:>2}. {item.queue_id}  {item.state.value:10}  {preview}")


def _print_memory_records(records) -> None:
    if not records:
        print("No matching long-term memories.")
        return
    for record in records:
        preview = record.text.replace("\n", " ")
        print(
            f"{record.memory_id}  {record.scope.value:9}  {record.category.value:10}  "
            f"importance={record.importance} sources={record.source_count}  {preview}"
        )


def _show_memory_status(runtime: AgentRuntime, session_id: str) -> None:
    counts = runtime.memory_status(session_id)
    print(
        f"Memory: visible={counts['visible']} total={counts['total']} "
        f"pending={counts['pending']}"
    )


def _print_help() -> None:
    print(
        "Commands:\n"
        "  /new [path]             create a fresh session, reusing current workspace unless path is given\n"
        "  /sessions               list saved sessions\n"
        "  /use <session-id>       switch to a saved session\n"
        "  /session                show current session id\n"
        "  /workspace              show current workspace path\n"
        "  /permissions            show current permission mode\n"
        "  /permissions <mode>     set read-only / approval / workspace / full-access\n"
        "  /goal                   show durable goal\n"
        "  /goal set <objective>   create/replace the active durable goal\n"
        "  /goal budget <n> <obj>  create goal with a model-token budget\n"
        "  /goal pause|resume      pause/resume goal continuation\n"
        "  /goal blocked|complete  mark the goal blocked/complete\n"
        "  /goal continue [n]      run up to n continuation turns (default 1)\n"
        "  /goal clear             remove the durable goal\n"
        "  /queue                  list queued future turns\n"
        "  /queue add <text>       append a durable future turn\n"
        "  /queue run [n]          synchronously drain queued turns\n"
        "  /queue remove <id>      remove one queued turn\n"
        "  /memory                 show long-term memory status\n"
        "  /memory extract         extract durable memories from this thread\n"
        "  /memory consolidate     consolidate pending memory candidates\n"
        "  /memory list [n]        list memories visible to this workspace\n"
        "  /memory search <query>  search long-term memory\n"
        "  /memory forget <id>     forget one visible long-term memory\n"
        "  /usage                  show current token usage\n"
        "  /help                   show this help\n"
        "  /quit                   exit Loom\n"
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
        if text == "/goal":
            _show_goal(runtime, session_id)
            continue
        if text.startswith("/goal set "):
            objective = text[len("/goal set "):].strip()
            try:
                runtime.set_goal(session_id, objective)
                _show_goal(runtime, session_id)
            except (ValueError, RuntimeError) as exc:
                print(f"Cannot set goal: {exc}", file=sys.stderr)
            continue
        if text.startswith("/goal budget "):
            rest = text[len("/goal budget "):].strip()
            pieces = rest.split(maxsplit=1)
            if len(pieces) != 2:
                print("Usage: /goal budget <tokens> <objective>", file=sys.stderr)
                continue
            try:
                budget = int(pieces[0])
                runtime.set_goal(session_id, pieces[1], token_budget=budget)
                _show_goal(runtime, session_id)
            except (ValueError, RuntimeError) as exc:
                print(f"Cannot set goal: {exc}", file=sys.stderr)
            continue
        if text in {"/goal pause", "/goal resume", "/goal blocked", "/goal complete"}:
            status = {
                "/goal pause": GoalStatus.PAUSED,
                "/goal resume": GoalStatus.ACTIVE,
                "/goal blocked": GoalStatus.BLOCKED,
                "/goal complete": GoalStatus.COMPLETE,
            }[text]
            try:
                runtime.set_goal_status(session_id, status)
                _show_goal(runtime, session_id)
            except (KeyError, ValueError, RuntimeError) as exc:
                print(f"Cannot update goal: {exc}", file=sys.stderr)
            continue
        if text == "/goal clear":
            runtime.clear_goal(session_id)
            print("Goal cleared.")
            continue
        if text == "/goal continue" or text.startswith("/goal continue "):
            raw = text[len("/goal continue"):].strip()
            try:
                turns = int(raw) if raw else 1
                result = runtime.continue_goal(session_id, max_turns=turns)
                result = _finish_result(runtime, result)
                _print_run_result(result)
            except (ValueError, RuntimeError) as exc:
                print(f"Cannot continue goal: {exc}", file=sys.stderr)
            continue
        if text == "/queue":
            _show_queue(runtime, session_id)
            continue
        if text.startswith("/queue add "):
            body = text[len("/queue add "):].strip()
            try:
                item = runtime.enqueue_turn(session_id, body)
                print(f"Queued: {item.queue_id}")
            except (ValueError, RuntimeError) as exc:
                print(f"Cannot queue turn: {exc}", file=sys.stderr)
            continue
        if text == "/queue run" or text.startswith("/queue run "):
            raw = text[len("/queue run"):].strip()
            try:
                turns = int(raw) if raw else None
                result = runtime.run_queued(session_id, max_turns=turns)
                if result is None:
                    print("Queue is empty.")
                else:
                    result = _finish_result(runtime, result)
                    _print_run_result(result)
            except (ValueError, RuntimeError) as exc:
                print(f"Cannot run queue: {exc}", file=sys.stderr)
            continue
        if text.startswith("/queue remove "):
            queue_id = text[len("/queue remove "):].strip()
            if runtime.remove_queued_turn(session_id, queue_id):
                print("Queued turn removed.")
            else:
                print("Queued turn not found.", file=sys.stderr)
            continue
        if text in {"/memory", "/memory status"}:
            _show_memory_status(runtime, session_id)
            continue
        if text == "/memory extract":
            try:
                result = runtime.extract_memory_from_thread(session_id)
                print(
                    f"Memory extraction: {result.extraction.candidate_count} candidate(s), "
                    f"{len(result.consolidated)} consolidated record(s), "
                    f"tokens={result.usage.total_tokens}."
                )
            except (ValueError, RuntimeError) as exc:
                print(f"Cannot extract memory: {exc}", file=sys.stderr)
            continue
        if text == "/memory consolidate":
            try:
                records = runtime.consolidate_memory(session_id=session_id)
                print(f"Consolidated {len(records)} memory record(s).")
            except (ValueError, RuntimeError) as exc:
                print(f"Cannot consolidate memory: {exc}", file=sys.stderr)
            continue
        if text == "/memory list" or text.startswith("/memory list "):
            raw = text[len("/memory list"):].strip()
            try:
                limit = int(raw) if raw else 50
                _print_memory_records(runtime.list_memory(session_id, limit=limit))
            except (ValueError, RuntimeError) as exc:
                print(f"Cannot list memory: {exc}", file=sys.stderr)
            continue
        if text.startswith("/memory search "):
            query = text[len("/memory search "):].strip()
            if not query:
                print("Usage: /memory search <query>", file=sys.stderr)
                continue
            try:
                _print_memory_records(runtime.search_memory(session_id, query, limit=20))
            except (ValueError, RuntimeError) as exc:
                print(f"Cannot search memory: {exc}", file=sys.stderr)
            continue
        if text.startswith("/memory forget "):
            memory_id = text[len("/memory forget "):].strip()
            if not memory_id:
                print("Usage: /memory forget <id>", file=sys.stderr)
                continue
            try:
                if runtime.forget_memory(session_id, memory_id):
                    print("Memory forgotten.")
                else:
                    print("Memory not found.", file=sys.stderr)
            except (PermissionError, ValueError, RuntimeError) as exc:
                print(f"Cannot forget memory: {exc}", file=sys.stderr)
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
