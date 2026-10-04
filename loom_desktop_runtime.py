from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path


def _prepare_frozen_environment() -> None:
    if not getattr(sys, "frozen", False):
        return
    loom_home = Path(os.environ.get("LOOM_HOME") or (Path.home() / ".loom")).expanduser().resolve()
    os.environ.setdefault("LOOM_COMPUTER_LOG_DIR", str(loom_home / "logs" / "computer-use"))
    os.environ.setdefault("LOOM_BROWSER_LOG_DIR", str(loom_home / "logs" / "browser-use"))
    mxc = Path(sys.executable).resolve().parent / "wxc-exec.exe"
    if mxc.is_file():
        os.environ.setdefault("LOOM_WINDOWS_SANDBOX_EXECUTABLE", str(mxc))
    browsers = Path(sys.executable).resolve().parent / "browsers"
    if browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
        for executable in sorted(browsers.glob("chromium-*/chrome-win*/chrome.exe")):
            os.environ.setdefault("LOOM_BUNDLED_BROWSER", str(executable))
            break


_prepare_frozen_environment()

from loom_app_server import main as app_server_main
from loom_chatgpt_mcp import main as chatgpt_mcp_main
from loom_ant_ling_bridge import main as ant_ling_bridge_main
from loom_model_admin import main as model_admin_main
from loom_model_bridge import main as model_bridge_main


def _replace_packaged_workspace(argv: list[str]) -> list[str]:
    if not getattr(sys, "frozen", False):
        return argv
    args = list(argv)
    workspace = Path.home() / "Documents" / "Loom Workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        index = args.index("--workspace")
    except ValueError:
        args.extend(["--workspace", str(workspace)])
        return args
    if index + 1 < len(args):
        args[index + 1] = str(workspace)
    else:
        args.append(str(workspace))
    return args


def _run_code(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write("argument expected for -c\n")
        return 2
    code, rest = argv[0], argv[1:]
    previous_argv = sys.argv
    sys.argv = ["-c", *rest]
    namespace = {"__name__": "__main__", "__file__": "<string>", "__package__": None}
    try:
        exec(compile(code, "<string>", "exec"), namespace, namespace)
    finally:
        sys.argv = previous_argv
    return 0


def _self_test() -> int:
    payload = {
        "ok": True,
        "runtime": "loom-desktop",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "frozen": bool(getattr(sys, "frozen", False)),
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0


def _first_run_test() -> int:
    """Release gate: empty home, no provider secrets or developer tooling."""
    import argparse
    import tempfile
    import importlib
    from loom_cli import _build_runtime
    for module in ("openai", "httpx", "jsonschema", "mcp.client.streamable_http",
                   "keyring.backends.Windows", "winpty", "PIL", "psutil",
                   "pyautogui", "pywinauto", "browser_use", "playwright"):
        importlib.import_module(module)
    if getattr(sys, "frozen", False):
        browser = Path(os.environ.get("LOOM_BUNDLED_BROWSER", ""))
        if not browser.is_file():
            raise RuntimeError("Release is missing its bundled Chromium browser")
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            instance = playwright.chromium.launch(executable_path=str(browser), headless=True)
            try:
                page = instance.new_page()
                page.set_content("<title>Loom first run</title><p>Browser ready</p>")
                assert page.title() == "Loom first run"
                assert page.screenshot().startswith(b"\x89PNG")
            finally:
                instance.close()
    with tempfile.TemporaryDirectory(prefix="loom-first-run-") as directory:
        args = argparse.Namespace(provider="openai-compatible",
            base_url="https://account.smirel.com/model/v1", model="Ling-3.0-flash",
            allow_unconfigured_model=True, vision=False, timeout=120, home=directory)
        runtime, store, _ = _build_runtime(args)
        try:
            session = runtime.create_session("agent.fast", workspace_dir=directory)
            assert store.load(session.session_id).session_id == session.session_id
        finally:
            runtime.close()
    print('{"ok":true,"first_run":"credential-free"}')
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write("usage: python {loom_app_server.py|loom_chatgpt_mcp.py|loom_model_bridge.py|loom_ant_ling_bridge.py|loom_model_admin.py|-c|self-test} ...\n")
        return 2

    first, rest = args[0], args[1:]
    if first == "-c":
        return _run_code(rest)
    if first == "self-test":
        return _self_test() if not rest else 2
    if first == "first-run-test":
        return _first_run_test() if not rest else 2

    script = Path(first).name.casefold()
    if script == "loom_app_server.py":
        return app_server_main(_replace_packaged_workspace(rest))
    if script == "loom_chatgpt_mcp.py":
        return chatgpt_mcp_main(_replace_packaged_workspace(rest))
    if script == "loom_model_bridge.py":
        return model_bridge_main(rest)
    if script == "loom_ant_ling_bridge.py":
        return ant_ling_bridge_main(rest)
    if script == "loom_model_admin.py":
        return model_admin_main(rest)

    sys.stderr.write(f"Loom packaged Python does not support entrypoint: {first}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
