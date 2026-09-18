from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.app_server_client import AppServerProcessConfig, LoomAppServerClient
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
        "--vision",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="forward the App Server model vision capability when explicitly set",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    backend = LoomAppServerClient(
        config.command(),
        request_timeout_seconds=max(30.0, float(args.timeout)),
    )
    backend.subscribe_stderr(lambda text: sys.stderr.write(f"[loom-app-server] {text}\n"))
    try:
        backend.start_and_initialize(client_name="loom-chatgpt-mcp", client_version="0.1.0")
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
