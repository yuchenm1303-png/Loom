from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import Sequence

from app.connectors import ConnectorError, ConnectorManager


def _runtime_home(value: str = "") -> Path:
    selected = str(value or os.environ.get("LOOM_HOME") or "").strip()
    return Path(selected or (Path.home() / ".loom")).expanduser().resolve()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loom connector", description="Manage Loom external service connectors")
    parser.add_argument("--home", default="", help="Loom runtime home; defaults to LOOM_HOME or ~/.loom")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("list", help="list connector status")

    github = subparsers.add_parser("github", help="manage the GitHub connector")
    github_sub = github.add_subparsers(dest="github_command")
    github_sub.add_parser("status", help="show GitHub connector status")
    login = github_sub.add_parser("login", help="start GitHub browser/device authorization")
    login.add_argument("--no-browser", action="store_true", help="do not open the verification page automatically")
    github_sub.add_parser("import-gh", help="import the current authenticated GitHub CLI credential into Loom's keychain")
    github_sub.add_parser("token", help="read a GitHub token securely from the terminal/stdin and store it in the OS keychain")
    github_sub.add_parser("logout", help="disconnect GitHub from Loom without modifying GitHub CLI or environment credentials")
    github_sub.add_parser("refresh", help="re-check available GitHub credentials and connection health")
    return parser


def _print(value: object, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if isinstance(value, dict):
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return
    print(value)


def _status_line(status: dict[str, object]) -> str:
    if bool(status.get("connected")):
        source = str(status.get("credentialSource") or "credential")
        account = str(status.get("account") or "unknown")
        return f"GitHub connected as {account} ({source})."
    if status.get("enabled") is False:
        return "GitHub is disconnected in Loom."
    detail = str(status.get("error") or "No credential found")
    return f"GitHub is not connected: {detail}"


def _token_from_terminal() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("GitHub token (input hidden): ").strip()
    return sys.stdin.readline().strip()


def _login(manager: ConnectorManager, *, open_browser: bool, as_json: bool) -> int:
    started = manager.start_github_auth()
    if as_json:
        _print(started, as_json=True)
    else:
        mode = str(started.get("mode") or "")
        url = str(started.get("verificationUrl") or "https://github.com/login/device")
        code = str(started.get("userCode") or "")
        if mode == "github-cli":
            print("GitHub CLI browser authorization started.")
            print("The one-time device code was copied to your clipboard by `gh`.")
        else:
            print(f"Open: {url}")
            print(f"Enter code: {code}")
            if open_browser:
                try:
                    webbrowser.open(url, new=2)
                except Exception:
                    pass

    session_id = str(started.get("sessionId") or "")
    interval = max(1.0, float(started.get("pollInterval") or 2))
    while True:
        time.sleep(interval)
        result = manager.poll_github_auth(session_id)
        interval = max(1.0, float(result.get("pollInterval") or interval))
        if str(result.get("status") or "") != "connected":
            continue
        connector = result.get("connector") if isinstance(result.get("connector"), dict) else manager.github_status()
        if as_json:
            _print(result, as_json=True)
        else:
            print(_status_line(dict(connector)))
        return 0


def run_connector_cli(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv or ()))
    if not args.command:
        _parser().print_help()
        return 2
    manager = ConnectorManager(_runtime_home(args.home))
    try:
        if args.command == "list":
            if args.json:
                _print({"connectors": manager.list()}, as_json=True)
            else:
                for status in manager.list():
                    print(_status_line(status))
            return 0

        if args.command != "github":
            raise ConnectorError(f"unsupported connector command: {args.command}")
        command = str(args.github_command or "status")
        if command == "status":
            status = manager.github_status()
            _print(status, as_json=True) if args.json else print(_status_line(status))
            return 0
        if command == "refresh":
            status = manager.refresh()
            _print(status, as_json=True) if args.json else print(_status_line(status))
            return 0 if status.get("connected") else 1
        if command == "import-gh":
            status = manager.import_github_cli()
            _print(status, as_json=True) if args.json else print(_status_line(status))
            return 0
        if command == "token":
            token = _token_from_terminal()
            if not token:
                raise ConnectorError("GitHub token input was empty")
            status = manager.connect_token(token)
            _print(status, as_json=True) if args.json else print(_status_line(status))
            return 0
        if command == "logout":
            status = manager.disconnect_github()
            _print(status, as_json=True) if args.json else print("GitHub disconnected from Loom. GitHub CLI and environment credentials were left unchanged.")
            return 0
        if command == "login":
            return _login(manager, open_browser=not bool(args.no_browser), as_json=bool(args.json))
        raise ConnectorError(f"unsupported GitHub connector command: {command}")
    except KeyboardInterrupt:
        print("\nGitHub authorization cancelled.", file=sys.stderr)
        return 130
    except ConnectorError as exc:
        if args.json:
            _print({"ok": False, "error": str(exc)}, as_json=True)
        else:
            print(f"Connector error: {exc}", file=sys.stderr)
        return 1


__all__ = ["run_connector_cli"]
