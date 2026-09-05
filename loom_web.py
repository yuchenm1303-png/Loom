from __future__ import annotations

import argparse
from pathlib import Path

from app.agent_runtime import AgentStatus, PermissionMode
from app.web_ui import serve_local_ui
from loom_cli import _build_runtime, _resolve_new_permission_mode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Loom local browser UI")
    parser.add_argument("--provider", choices=["openai", "openai-compatible"])
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--home", help="runtime state root; defaults to ~/.loom")
    parser.add_argument("--workspace", help="default workspace for a new UI session")
    parser.add_argument("--session", help="open an existing Loom session")
    parser.add_argument(
        "--permission-mode",
        choices=[mode.value for mode in PermissionMode],
        help="default mode for new sessions, or explicit override for --session",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.session and args.workspace:
        raise SystemExit("--workspace cannot be combined with --session; resumed sessions keep their saved workspace.")

    runtime, store, model = _build_runtime(args)
    preferred_session_id = ""

    if args.session:
        session = runtime.get_session(args.session)
        if session.status is AgentStatus.RUNNING:
            runtime.recover_interrupted(args.session)
            session = runtime.get_session(args.session)
        if args.permission_mode:
            session = runtime.set_permission_mode(args.session, PermissionMode(args.permission_mode))
        default_workspace = Path(session.workspace_dir)
        default_permission = session.permission_mode
        preferred_session_id = session.session_id
    else:
        default_workspace = Path(args.workspace or Path.cwd()).expanduser().resolve()
        if not default_workspace.exists():
            raise SystemExit(f"Workspace does not exist: {default_workspace}")
        if not default_workspace.is_dir():
            raise SystemExit(f"Workspace is not a directory: {default_workspace}")
        default_permission = _resolve_new_permission_mode(args)

    return serve_local_ui(
        runtime=runtime,
        store=store,
        model=model,
        default_workspace=default_workspace,
        default_permission_mode=default_permission,
        preferred_session_id=preferred_session_id,
        port=args.port,
        open_browser=not args.no_open_browser,
    )


if __name__ == "__main__":
    raise SystemExit(main())
