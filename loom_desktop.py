from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.app_server_client import AppServerProcessConfig, LoomAppServerClient


_PERMISSION_MODES = ("read-only", "approval", "workspace", "full-access")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Loom native desktop client")
    parser.add_argument("--provider", choices=["openai", "openai-compatible"])
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--home", help="runtime state root; defaults to ~/.loom")
    parser.add_argument("--workspace", help="default workspace; defaults to the current directory")
    parser.add_argument(
        "--permission-mode",
        choices=_PERMISSION_MODES,
        help="default permission mode for new threads",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--app-server-executable",
        help="optional packaged loom-app-server executable; source installs use the current Python environment",
    )
    return parser


def _workspace(value: str | None) -> Path:
    workspace = Path(value or Path.cwd()).expanduser().resolve()
    if not workspace.exists():
        raise SystemExit(f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise SystemExit(f"Workspace is not a directory: {workspace}")
    return workspace


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = _workspace(args.workspace)

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        from app.desktop_ui import LoomDesktopWindow
    except ImportError as exc:
        raise SystemExit(
            'Loom Desktop requires PySide6. Install the desktop extra with: '
            'python -m pip install -e ".[desktop]"'
        ) from exc

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Loom")
    app.setOrganizationName("Loom")

    config = AppServerProcessConfig(
        workspace=workspace,
        provider=args.provider,
        base_url=args.base_url,
        model=args.model,
        home=args.home,
        permission_mode=args.permission_mode,
        timeout_seconds=args.timeout,
        app_server_executable=args.app_server_executable,
    )
    client = LoomAppServerClient(
        config.command(),
        request_timeout_seconds=max(10.0, min(float(args.timeout), 120.0)),
    )
    try:
        initialization = client.start_and_initialize(client_name="loom-desktop", client_version="0.1")
    except Exception as exc:
        client.close()
        QMessageBox.critical(
            None,
            "Loom could not start",
            f"The local Loom App Server could not be initialized.\n\n{type(exc).__name__}: {exc}",
        )
        return 1

    runtime = initialization.get("runtime") or {}
    default_permission = args.permission_mode or runtime.get("defaultPermissionMode") or "approval"
    window = LoomDesktopWindow(
        client=client,
        initialization=initialization,
        default_workspace=workspace,
        default_permission_mode=str(default_permission),
    )
    window.show()
    try:
        return int(app.exec())
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
