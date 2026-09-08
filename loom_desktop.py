from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping

from app.ai.model_store import ModelConfigStore
from app.app_server_client import AppServerProcessConfig, LoomAppServerClient


_PERMISSION_MODES = ("read-only", "approval", "workspace", "full-access")
_MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
_MINIMAX_DEFAULT_MODEL = "MiniMax-M3"
_PRIMARY_MINIMAX_KEY_ENV = ("MINIMAX_API_KEY", "LOOM_PRIMARY_API_KEY", "LOOM_API_KEY")


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


def _primary_minimax_key(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the Desktop Agent key without borrowing Computer Use credentials.

    ``DASHSCOPE_API_KEY`` is intentionally absent here. That credential belongs
    to the Alibaba GUI-Plus Computer Use grounder and must never silently turn
    Qwen into Loom's primary conversational/tool-using model.
    """

    env = os.environ if environ is None else environ
    for name in _PRIMARY_MINIMAX_KEY_ENV:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return ""


def _default_primary_model(
    selection: str | None,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str, str, str]:
    """Return Loom Desktop's default primary Agent connection.

    MiniMax uses its OpenAI-compatible endpoint, so it can reuse Loom's existing
    streaming/tool-call backend while remaining completely separate from the
    Qwen/GUI-Plus Computer Use credential lane.
    """

    api_key = _primary_minimax_key(environ)
    if not api_key:
        raise RuntimeError(
            "MiniMax primary API key is not configured. Set MINIMAX_API_KEY / "
            "LOOM_PRIMARY_API_KEY, or add a saved MiniMax model connection."
        )
    model = str(selection or _MINIMAX_DEFAULT_MODEL).strip() or _MINIMAX_DEFAULT_MODEL
    return "openai-compatible", _MINIMAX_BASE_URL, model, api_key


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = _workspace(args.workspace)

    # The UI and launcher both open the same model registry. When --home is
    # explicit, publish it before importing the desktop package so there is one
    # source of truth for the whole process.
    if args.home:
        os.environ["LOOM_HOME"] = str(Path(args.home).expanduser().resolve())
    model_store = ModelConfigStore(args.home)

    try:
        from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
        from app.desktop.composer import AddModelDialog
        from app.desktop_ui import LoomDesktopWindow
    except ImportError as exc:
        raise SystemExit(
            'Loom Desktop requires PySide6. Install the desktop extra with: '
            'python -m pip install -e ".[desktop]"'
        ) from exc

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Loom")
    app.setOrganizationName("Loom")

    explicit_launch = any((args.provider, args.base_url, args.model))

    def start_server(selection: str | None) -> tuple[LoomAppServerClient, dict]:
        """Launch an App Server for a raw model name or a saved API profile.

        Saved profiles carry provider endpoint metadata only. Their API key is
        resolved from the OS credential store here, copied into the child
        process environment, and never sent through the App Server protocol.

        With no explicit legacy CLI flags and no saved selection, Loom Desktop
        uses MiniMax as its primary Agent model. DashScope stays in the inherited
        environment for Computer Use, but is never copied into ``LOOM_API_KEY``.
        """
        saved = model_store.model_for_selection(selection)
        child_env: dict[str, str] | None = None
        if saved is not None:
            provider = saved.adapter.value
            base_url = saved.base_url or None
            model = saved.model
            child_env = os.environ.copy()
            child_env["LOOM_API_KEY"] = model_store.secret_for(saved)
        elif explicit_launch:
            provider = args.provider
            base_url = args.base_url
            model = selection if selection is not None else args.model
        else:
            # "Other model…" means another model on the currently selected API
            # connection. Preserve that behavior for a saved provider profile.
            active_saved = model_store.active_model() if selection is not None else None
            if active_saved is not None:
                provider = active_saved.adapter.value
                base_url = active_saved.base_url or None
                model = selection
                child_env = os.environ.copy()
                child_env["LOOM_API_KEY"] = model_store.secret_for(active_saved)
            else:
                provider, base_url, model, primary_key = _default_primary_model(selection)
                child_env = os.environ.copy()
                child_env["LOOM_API_KEY"] = primary_key

        config = AppServerProcessConfig(
            workspace=workspace,
            provider=provider,
            base_url=base_url,
            model=model,
            home=args.home,
            permission_mode=args.permission_mode,
            timeout_seconds=args.timeout,
            app_server_executable=args.app_server_executable,
        )
        server = LoomAppServerClient(
            config.command(),
            env=child_env,
            request_timeout_seconds=max(10.0, min(float(args.timeout), 120.0)),
        )
        try:
            handshake = server.start_and_initialize(
                client_name="loom-desktop", client_version="0.1"
            )
            if saved is not None:
                model_store.set_active(saved.model_id)
            elif model_store.active_model_id is not None and not (
                selection is not None and not explicit_launch
            ):
                model_store.set_active(None)
        except Exception:
            server.close()
            raise
        return server, handshake

    # Explicit legacy CLI flags win. Otherwise a saved model becomes the next
    # launch default. If there is no saved primary model yet, MiniMax is the
    # Desktop default and Loom offers a one-time secure setup dialog when its key
    # is not already available from the primary-model environment variables.
    initial_selection = args.model
    if not explicit_launch:
        try:
            active = model_store.active_model()
        except Exception:
            active = None
        if active is not None:
            initial_selection = active.selection
        elif not _primary_minimax_key():
            dialog = AddModelDialog()
            dialog.setWindowTitle("Connect MiniMax")
            dialog.name_edit.setText("MiniMax M2.7")
            dialog.base_url_edit.setText(_MINIMAX_BASE_URL)
            dialog.model_edit.setText(_MINIMAX_DEFAULT_MODEL)
            dialog.api_key_edit.setPlaceholderText("MiniMax API key (stored in the OS credential store)")
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return 0
            try:
                entry = model_store.save_model(**dialog.values())
                model_store.set_active(entry.model_id)
                initial_selection = entry.selection
            except Exception as exc:
                QMessageBox.critical(
                    None,
                    "MiniMax could not be saved",
                    f"Loom could not save the MiniMax model connection.\n\n{type(exc).__name__}: {exc}",
                )
                return 1

    try:
        client, initialization = start_server(initial_selection)
    except Exception as exc:
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
        client_factory=start_server,
    )
    window.show()
    try:
        return int(app.exec())
    finally:
        # Switching model replaces the window's client, so close whichever
        # server process it actually ended up owning.
        window.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
