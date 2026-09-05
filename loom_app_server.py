from __future__ import annotations

import argparse
from pathlib import Path

from app.agent_runtime import PermissionMode
from app.app_server import serve_stdio
from loom_cli import _build_runtime, _resolve_new_permission_mode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Loom local app-server (stdio JSON-RPC)")
    parser.add_argument("--provider", choices=["openai", "openai-compatible"])
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--home", help="runtime state root; defaults to ~/.loom")
    parser.add_argument("--workspace", help="default workspace for new threads")
    parser.add_argument(
        "--permission-mode",
        choices=[mode.value for mode in PermissionMode],
        help="default permission mode for new threads",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = Path(args.workspace or Path.cwd()).expanduser().resolve()
    if not workspace.exists():
        raise SystemExit(f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise SystemExit(f"Workspace is not a directory: {workspace}")

    # stdout is reserved exclusively for JSON-RPC protocol frames. Runtime
    # construction is intentionally reused from the CLI so credentials and
    # provider configuration never enter client-visible protocol state.
    runtime, store, model = _build_runtime(args)
    permission_mode = _resolve_new_permission_mode(args)
    return serve_stdio(
        runtime=runtime,
        store=store,
        model=model,
        default_workspace=workspace,
        default_permission_mode=permission_mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
