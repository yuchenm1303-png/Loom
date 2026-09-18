from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.app_server_client import AppServerProcessConfig, LoomAppServerClient
from app.app_server_local_ipc import (
    LocalAppServerUnavailable,
    LoomLocalAppServerClient,
    resolve_runtime_home,
)
from app.chatgpt_mcp import build_mcp_server
from app.remote_control import RemoteControlClient, RemoteControlPolicy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Loom ChatGPT MCP adapter (stdio MCP -> Loom App Server)"
    )
    parser.add_argument("--workspace", default=str(Path.cwd()))
    parser.add_argument("--home")
    parser.add_argument("--provider", choices=["openai", "openai-compatible"])
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--app-server-executable")
    parser.add_argument(
        "--local-attach",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "attach to the running canonical App Server; use --no-local-attach "
            "only to start an explicit standalone canonical App Server"
        ),
    )
    parser.add_argument(
        "--vision",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="forward the App Server model vision capability when explicitly set",
    )
    return parser


def _connect_backend(args):
    timeout = max(30.0, float(args.timeout))

    if args.local_attach:
        local_backend = LoomLocalAppServerClient(
            resolve_runtime_home(args.home),
            request_timeout_seconds=timeout,
        )
        try:
            local_backend.connect_and_initialize(
                client_name="loom-chatgpt-mcp",
                client_version="0.1.0",
            )
        except LocalAppServerUnavailable as exc:
            local_backend.close()
            raise SystemExit(
                "Loom's shared App Server is not running. Start Loom Desktop first. "
                "Use --no-local-attach only for an explicit standalone/development runtime."
            ) from exc
        return local_backend

    config = AppServerProcessConfig(
        workspace=Path(args.workspace).expanduser().resolve(),
        provider=args.provider,
        base_url=args.base_url,
        model=args.model,
        home=args.home,
        timeout_seconds=float(args.timeout),
        app_server_executable=args.app_server_executable,
        vision=args.vision,
    )
    command = config.command()
    command.append("--local-ipc")
    child_backend = LoomAppServerClient(
        command,
        request_timeout_seconds=timeout,
    )
    child_backend.subscribe_stderr(
        lambda text: sys.stderr.write(f"[loom-app-server] {text}\n")
    )
    child_backend.start_and_initialize(
        client_name="loom-chatgpt-mcp",
        client_version="0.1.0",
    )
    return child_backend


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    backend = _connect_backend(args)

    try:
        remote = RemoteControlClient(
            backend,
            policy=RemoteControlPolicy(
                channel="chatgpt",
                new_thread_permission_mode="approval",
                allowed_active_permission_modes=("read-only", "approval"),
            ),
        )
        build_mcp_server(remote).run()
        return 0
    finally:
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
