from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TextIO

from app.agent_runtime import PermissionMode
from app.app_server_streaming import serve_streaming_stdio
from loom_cli import _build_runtime, _resolve_new_permission_mode


def _reconfigure_utf8(stream: TextIO, *, errors: str) -> None:
    """Force the stdio protocol stream to UTF-8 regardless of Windows locale."""

    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors=errors)


def _configure_protocol_stdio() -> None:
    """Make the JSONL wire encoding deterministic on every host.

    Python normally inherits the Windows active code page for redirected stdio.
    Loom Desktop always speaks UTF-8 JSON-RPC, so a Chinese/GBK locale could
    otherwise corrupt both incoming prompts and outgoing assistant text before
    Qt ever sees them.
    """

    _reconfigure_utf8(sys.stdin, errors="strict")
    _reconfigure_utf8(sys.stdout, errors="strict")
    # stderr is diagnostic rather than protocol data; preserving the process is
    # more useful than failing on an unencodable diagnostic character.
    _reconfigure_utf8(sys.stderr, errors="replace")


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
    # JSON-RPC v1 explicitly uses UTF-8 JSONL. Configure this before parsing or
    # runtime startup so locale-dependent redirected stdio cannot enter the
    # protocol path on Windows.
    _configure_protocol_stdio()

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
    return serve_streaming_stdio(
        runtime=runtime,
        store=store,
        model=model,
        default_workspace=workspace,
        default_permission_mode=permission_mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
