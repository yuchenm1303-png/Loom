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


_prepare_frozen_environment()

from loom_app_server import main as app_server_main
from loom_chatgpt_mcp import main as chatgpt_mcp_main
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        sys.stderr.write("usage: python {loom_app_server.py|loom_chatgpt_mcp.py|loom_model_bridge.py|loom_model_admin.py|-c|self-test} ...\n")
        return 2

    first, rest = args[0], args[1:]
    if first == "-c":
        return _run_code(rest)
    if first == "self-test":
        return _self_test() if not rest else 2

    script = Path(first).name.casefold()
    if script == "loom_app_server.py":
        return app_server_main(_replace_packaged_workspace(rest))
    if script == "loom_chatgpt_mcp.py":
        return chatgpt_mcp_main(_replace_packaged_workspace(rest))
    if script == "loom_model_bridge.py":
        return model_bridge_main(rest)
    if script == "loom_model_admin.py":
        return model_admin_main(rest)

    sys.stderr.write(f"Loom packaged Python does not support entrypoint: {first}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
