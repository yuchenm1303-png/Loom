from __future__ import annotations

import argparse
import os
import secrets
import time
from pathlib import Path

from app.agent_runtime import PermissionMode
from app.app_server_client import AppServerProcessConfig, LoomAppServerClient
from app.remote.bridge import WeChatRemoteBridge
from app.remote.state import WeChatRemoteStateStore
from app.remote.wechat_customer_service import (
    WeChatCustomerServiceClient,
    WeChatCustomerServiceConfig,
    WeChatCustomerServiceError,
    WeChatInboundMessage,
)


def _env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _home(value: str | None) -> Path:
    return Path(value or _env("LOOM_HOME") or (Path.home() / ".loom")).expanduser().resolve()


def _pairing_code(value: str = "") -> str:
    supplied = str(value or "").strip()
    if supplied:
        if not supplied.isdigit() or not 4 <= len(supplied) <= 12:
            raise SystemExit("--pairing-code must contain 4 to 12 digits")
        return supplied
    return f"{secrets.randbelow(100_000_000):08d}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Use WeChat Customer Service as a personal remote control for Loom"
    )
    parser.add_argument("--workspace", default=".", help="workspace used by new remote Loom threads")
    parser.add_argument("--home", help="Loom state root; defaults to LOOM_HOME or ~/.loom")
    parser.add_argument("--provider", choices=["openai", "openai-compatible"])
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--permission-mode",
        choices=[mode.value for mode in PermissionMode],
        default=PermissionMode.APPROVAL.value,
        help="permission mode for new remote threads; approval is the safe default",
    )
    parser.add_argument(
        "--vision",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="declare that the selected model can read images",
    )
    parser.add_argument("--corp-id", default=_env("LOOM_WECOM_CORP_ID"))
    parser.add_argument(
        "--open-kfid",
        default=_env("LOOM_WECOM_OPEN_KFID"),
        help="WeChat Customer Service account id; auto-detected when exactly one account exists",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(_env("LOOM_WECOM_POLL_INTERVAL") or "30"),
        help="seconds between sync_msg polls; bootstrap polling intentionally stays conservative",
    )
    parser.add_argument(
        "--pairing-code",
        default="",
        help="optional numeric code used by /bind; generated when omitted",
    )
    parser.add_argument(
        "--reset-binding",
        action="store_true",
        help="forget the currently paired WeChat user before starting",
    )
    return parser


def _required(value: str, *, flag: str, env_name: str) -> str:
    text = str(value or "").strip()
    if text:
        return text
    raise SystemExit(f"Missing {flag}. Pass {flag} or set {env_name}.")


def _resolve_open_kf_id(
    *,
    corp_id: str,
    secret: str,
    requested: str,
    timeout_seconds: float,
) -> tuple[str, WeChatCustomerServiceClient]:
    discovery = WeChatCustomerServiceClient(
        WeChatCustomerServiceConfig(
            corp_id=corp_id,
            secret=secret,
            timeout_seconds=timeout_seconds,
        )
    )
    account_id = str(requested or "").strip()
    if not account_id:
        accounts = discovery.list_accounts()
        if not accounts:
            raise SystemExit(
                "No WeChat Customer Service account is available. Create one in the "
                "WeCom admin console and enable API management first."
            )
        if len(accounts) > 1:
            choices = "\n".join(
                f"  {str(item.get('name') or 'unnamed')}: {str(item.get('open_kfid') or '')}"
                for item in accounts
            )
            raise SystemExit(
                "Multiple WeChat Customer Service accounts are available. "
                "Pass --open-kfid or set LOOM_WECOM_OPEN_KFID:\n" + choices
            )
        account_id = str(accounts[0].get("open_kfid") or "").strip()
    if not account_id:
        raise SystemExit("WeChat Customer Service account has no open_kfid.")

    client = WeChatCustomerServiceClient(
        WeChatCustomerServiceConfig(
            corp_id=corp_id,
            secret=secret,
            open_kf_id=account_id,
            timeout_seconds=timeout_seconds,
        )
    )
    return account_id, client


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise SystemExit(f"Workspace does not exist or is not a directory: {workspace}")

    home = _home(args.home)
    state = WeChatRemoteStateStore(home)
    if args.reset_binding:
        state.reset_binding()

    corp_id = _required(args.corp_id, flag="--corp-id", env_name="LOOM_WECOM_CORP_ID")
    secret = _required(
        _env("LOOM_WECOM_KF_SECRET"),
        flag="LOOM_WECOM_KF_SECRET",
        env_name="LOOM_WECOM_KF_SECRET",
    )
    timeout_seconds = min(30.0, max(5.0, float(args.timeout)))
    open_kfid, wechat = _resolve_open_kf_id(
        corp_id=corp_id,
        secret=secret,
        requested=args.open_kfid,
        timeout_seconds=timeout_seconds,
    )

    interval = max(10.0, float(args.poll_interval))
    pair_code = "" if state.binding is not None else _pairing_code(args.pairing_code)

    process_config = AppServerProcessConfig(
        workspace=workspace,
        provider=args.provider,
        base_url=args.base_url,
        model=args.model,
        home=home,
        permission_mode=args.permission_mode,
        timeout_seconds=args.timeout,
        vision=args.vision,
    )
    app_client = LoomAppServerClient(
        process_config.command(),
        cwd=Path(__file__).resolve().parent,
        request_timeout_seconds=max(30.0, float(args.timeout) + 10.0),
    )
    bridge = WeChatRemoteBridge(
        app_client=app_client,
        wechat=wechat,
        state=state,
        workspace=workspace,
        pairing_code=pair_code,
        permission_mode=args.permission_mode,
        log=lambda line: print(line, flush=True),
    )

    app_client.subscribe_stderr(lambda line: print(f"[loom-app-server] {line}", flush=True))
    app_client.subscribe_exit(lambda line: print(f"[loom-app-server] {line}", flush=True))
    app_client.start_and_initialize(client_name="loom-remote-wechat", client_version="0.1")

    print("Loom WeChat Remote is running.", flush=True)
    print(f"Workspace: {workspace}", flush=True)
    print(f"WeChat Customer Service: {open_kfid}", flush=True)
    print(f"Polling interval: {interval:g}s", flush=True)
    try:
        contact_url = wechat.contact_url(scene="loom-remote")
    except Exception as exc:
        contact_url = ""
        print(
            f"[remote-wechat] could not create contact URL: {type(exc).__name__}: {exc}",
            flush=True,
        )
    if contact_url:
        print(f"WeChat entry: {contact_url}", flush=True)

    if state.binding is None:
        print("", flush=True)
        print("Pair this WeChat remote by sending the following message to the customer-service chat:", flush=True)
        print(f"  /bind {pair_code}", flush=True)
        print("Until pairing succeeds, messages from all other WeChat users are ignored.", flush=True)
    else:
        binding = state.binding
        print(
            f"Paired WeChat user: {binding.external_user_id[:10]}…"
            + (f" | Loom thread: {binding.thread_id[:8]}" if binding.thread_id else ""),
            flush=True,
        )

    try:
        while True:
            try:
                pages = 0
                while True:
                    pages += 1
                    raw_messages, next_cursor, has_more = wechat.sync_messages(
                        cursor=state.cursor,
                        limit=100,
                    )
                    for raw in raw_messages:
                        message = WeChatInboundMessage.from_api(raw)
                        if message is None:
                            continue
                        if not state.accept_message(message.message_id):
                            continue
                        bridge.handle_message(message)
                    if next_cursor != state.cursor:
                        state.set_cursor(next_cursor)
                    if not has_more or pages >= 20:
                        break
            except WeChatCustomerServiceError as exc:
                print(
                    f"[remote-wechat] WeChat API error"
                    f"{f' {exc.errcode}' if exc.errcode is not None else ''}: {exc}",
                    flush=True,
                )
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopping Loom WeChat Remote.", flush=True)
    finally:
        app_client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
