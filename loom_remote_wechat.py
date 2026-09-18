from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from app.agent_runtime import PermissionMode
from app.app_server_client import AppServerProcessConfig, LoomAppServerClient
from app.remote.channels.weixin import (
    DEFAULT_ILINK_BASE_URL,
    WeixinApiClient,
    WeixinApiError,
    WeixinAuthenticationExpired,
    WeixinChannel,
    WeixinCredentials,
    WeixinCredentialStore,
    WeixinMonitor,
    WeixinQrAuthenticator,
    WeixinRemoteStateStore,
)
from app.remote.service import LoomRemoteService


def _env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _home(value: str | None) -> Path:
    return Path(value or _env("LOOM_HOME") or (Path.home() / ".loom")).expanduser().resolve()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Use a personal WeChat account through Tencent iLink as a remote control for Loom"
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
    parser.add_argument(
        "--qr-timeout",
        type=float,
        default=480.0,
        help="seconds to wait for QR login confirmation when a login is required",
    )
    parser.add_argument(
        "--ilink-timeout",
        type=float,
        default=15.0,
        help="HTTP timeout in seconds for non-long-poll iLink requests",
    )
    parser.add_argument(
        "--reset-login",
        "--reset-binding",
        dest="reset_login",
        action="store_true",
        help="forget the saved personal-WeChat login and bind again by QR code",
    )
    return parser


def _restore_credentials(
    state: WeixinRemoteStateStore,
    store: WeixinCredentialStore,
) -> WeixinCredentials | None:
    binding = state.binding
    if binding is None:
        return None
    token = store.get_bot_token(binding.ilink_bot_id)
    if not token:
        return None
    return WeixinCredentials(
        bot_token=token,
        ilink_bot_id=binding.ilink_bot_id,
        base_url=binding.base_url or DEFAULT_ILINK_BASE_URL,
        ilink_user_id=binding.ilink_user_id,
    )


def _login(
    *,
    api: WeixinApiClient,
    state: WeixinRemoteStateStore,
    store: WeixinCredentialStore,
    timeout_seconds: float,
    existing: WeixinCredentials | None = None,
) -> WeixinCredentials:
    old_binding = state.binding
    local_tokens = [existing.bot_token] if existing is not None and existing.bot_token else []
    authenticator = WeixinQrAuthenticator(
        api,
        log=lambda line: print(line, flush=True),
    )
    credentials = authenticator.login(
        local_tokens=local_tokens,
        existing_credentials=existing,
        timeout_seconds=timeout_seconds,
    )

    # Persist the secret first; the JSON state only references its bot id.
    store.set_bot_token(credentials.ilink_bot_id, credentials.bot_token)
    state.bind_login(
        credentials.ilink_bot_id,
        credentials.ilink_user_id,
        credentials.base_url,
        bound_at_ms=int(time.time() * 1000),
    )
    if old_binding is not None and old_binding.ilink_bot_id != credentials.ilink_bot_id:
        try:
            store.delete_bot_token(old_binding.ilink_bot_id)
        except RuntimeError:
            pass
    return credentials


def _best_effort_notify(
    api: WeixinApiClient,
    credentials: WeixinCredentials,
    *,
    starting: bool,
) -> None:
    try:
        if starting:
            api.notify_start(credentials)
        else:
            api.notify_stop(credentials)
    except Exception as exc:
        print(
            f"[remote-weixin] lifecycle notification failed: {type(exc).__name__}",
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise SystemExit(f"Workspace does not exist or is not a directory: {workspace}")

    home = _home(args.home)
    state = WeixinRemoteStateStore(home)
    credentials_store = WeixinCredentialStore()

    if args.reset_login:
        old = state.binding
        if old is not None:
            try:
                credentials_store.delete_bot_token(old.ilink_bot_id)
            except RuntimeError as exc:
                raise SystemExit(str(exc)) from exc
        state.reset()

    api = WeixinApiClient(timeout_seconds=max(1.0, float(args.ilink_timeout)))
    try:
        credentials = _restore_credentials(state, credentials_store)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    if credentials is None:
        print("Loom Weixin Remote needs a one-time personal-WeChat QR login.", flush=True)
        try:
            credentials = _login(
                api=api,
                state=state,
                store=credentials_store,
                timeout_seconds=args.qr_timeout,
            )
        except (RuntimeError, TimeoutError, WeixinApiError) as exc:
            raise SystemExit(f"Weixin QR login failed: {type(exc).__name__}: {exc}") from exc
        print("✅ Weixin QR login completed and the token was saved to the OS keyring.", flush=True)
    else:
        print("Restored the saved personal-WeChat login from the OS keyring.", flush=True)

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
    channel = WeixinChannel(
        api=api,
        credentials=credentials,
        state=state,
        log=lambda line: print(line, flush=True),
    )
    service = LoomRemoteService(
        app_client=app_client,
        channel=channel,
        state=state,
        workspace=workspace,
        permission_mode=args.permission_mode,
        log=lambda line: print(line, flush=True),
    )
    channel.attach_service(service)

    app_client.subscribe_stderr(lambda line: print(f"[loom-app-server] {line}", flush=True))
    app_client.subscribe_exit(lambda line: print(f"[loom-app-server] {line}", flush=True))
    app_client.start_and_initialize(client_name="loom-remote-wechat", client_version="0.2")

    binding = state.binding
    print("Loom Weixin Remote is running.", flush=True)
    print(f"Workspace: {workspace}", flush=True)
    if binding is not None:
        print(
            f"Weixin bot: {binding.ilink_bot_id[:12]}… | bound user: {binding.ilink_user_id[:12]}…",
            flush=True,
        )
    print("Only text messages from the QR-authorized WeChat identity will be executed.", flush=True)

    current_credentials = credentials
    _best_effort_notify(api, current_credentials, starting=True)
    try:
        while True:
            monitor = WeixinMonitor(
                api=api,
                state=state,
                credentials=current_credentials,
                on_message=channel.handle_inbound,
                log=lambda line: print(line, flush=True),
            )
            try:
                monitor.run()
                break
            except WeixinAuthenticationExpired:
                print(
                    "[remote-weixin] saved iLink login is no longer valid; QR login is required again.",
                    flush=True,
                )
                stale_bot_id = current_credentials.ilink_bot_id
                try:
                    credentials_store.delete_bot_token(stale_bot_id)
                except RuntimeError:
                    pass
                try:
                    current_credentials = _login(
                        api=api,
                        state=state,
                        store=credentials_store,
                        timeout_seconds=args.qr_timeout,
                        existing=None,
                    )
                except (RuntimeError, TimeoutError, WeixinApiError) as exc:
                    raise SystemExit(
                        f"Weixin re-login failed: {type(exc).__name__}: {exc}"
                    ) from exc
                channel.update_credentials(current_credentials)
                _best_effort_notify(api, current_credentials, starting=True)
    except KeyboardInterrupt:
        print("\nStopping Loom Weixin Remote.", flush=True)
    finally:
        _best_effort_notify(api, current_credentials, starting=False)
        app_client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
