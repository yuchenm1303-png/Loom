from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import TextIO
import sys

from .api import (
    DEFAULT_ILINK_BASE_URL,
    WeixinApiClient,
    WeixinApiError,
    WeixinCredentials,
    WeixinQrCode,
)


QrDisplay = Callable[[WeixinQrCode], None]
VerifyCodeProvider = Callable[[str], str]
LogListener = Callable[[str], None]


def display_qrcode(qr: WeixinQrCode, *, stream: TextIO | None = None) -> None:
    out = stream or sys.stdout
    print("请使用普通微信扫描二维码并确认授权：", file=out, flush=True)
    try:
        import qrcode

        terminal_qr = qrcode.QRCode(border=1)
        terminal_qr.add_data(qr.image_content)
        terminal_qr.make(fit=True)
        terminal_qr.print_ascii(out=out, invert=True)
    except Exception:
        print(qr.image_content, file=out, flush=True)
    print("如果终端二维码无法扫描，可复制上面的二维码内容到支持打开链接的设备。", file=out, flush=True)


class WeixinQrAuthenticator:
    def __init__(
        self,
        api: WeixinApiClient,
        *,
        log: LogListener | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api = api
        self.log = log or (lambda _message: None)
        self._sleep = sleep
        self._monotonic = monotonic

    def login(
        self,
        *,
        local_tokens: Iterable[str] = (),
        existing_credentials: WeixinCredentials | None = None,
        timeout_seconds: float = 480.0,
        bot_type: str = "3",
        on_qr: QrDisplay = display_qrcode,
        verify_code_provider: VerifyCodeProvider | None = None,
    ) -> WeixinCredentials:
        deadline = self._monotonic() + max(1.0, float(timeout_seconds))
        refreshes = 0
        max_refreshes = 3
        qr = self.api.get_bot_qrcode(
            local_token_list=local_tokens,
            bot_type=bot_type,
        )
        on_qr(qr)
        current_base_url = DEFAULT_ILINK_BASE_URL
        pending_verify_code = ""

        while self._monotonic() < deadline:
            try:
                status = self.api.get_qrcode_status(
                    qr.qrcode,
                    base_url=current_base_url,
                    verify_code=pending_verify_code,
                )
            except WeixinApiError as exc:
                self.log(f"[remote-weixin] QR status request failed: {type(exc).__name__}")
                self._sleep(1.0)
                continue

            state = status.status
            if state == "wait":
                self._sleep(1.0)
                continue

            if state == "scaned":
                if pending_verify_code:
                    pending_verify_code = ""
                self.log("[remote-weixin] QR scanned; waiting for confirmation")
                self._sleep(1.0)
                continue

            if state == "need_verifycode":
                provider = verify_code_provider or (
                    lambda prompt: input(prompt).strip()
                )
                code = str(provider("请输入手机微信显示的数字验证码：") or "").strip()
                if not code:
                    raise RuntimeError("Weixin verification code was empty")
                pending_verify_code = code
                continue

            if state == "scaned_but_redirect":
                host = status.redirect_host.strip()
                if host:
                    if host.startswith("http://") or host.startswith("https://"):
                        current_base_url = host.rstrip("/")
                    else:
                        current_base_url = f"https://{host.strip('/')}"
                    self.log("[remote-weixin] QR login switched to the server redirect host")
                self._sleep(1.0)
                continue

            if state == "binded_redirect":
                if existing_credentials is not None:
                    self.log("[remote-weixin] this Weixin bot is already linked; reusing saved login")
                    return existing_credentials
                raise RuntimeError(
                    "This Weixin bot is already linked, but Loom has no matching saved credential"
                )

            if state in {"expired", "verify_code_blocked"}:
                refreshes += 1
                if refreshes >= max_refreshes:
                    raise RuntimeError("Weixin QR login expired too many times")
                pending_verify_code = ""
                self.log("[remote-weixin] refreshing the QR code")
                qr = self.api.get_bot_qrcode(
                    local_token_list=local_tokens,
                    bot_type=bot_type,
                )
                current_base_url = DEFAULT_ILINK_BASE_URL
                on_qr(qr)
                continue

            if state == "confirmed":
                token = status.bot_token.strip()
                bot_id = status.ilink_bot_id.strip()
                user_id = status.ilink_user_id.strip()
                base_url = status.base_url.strip() or current_base_url
                if not token or not bot_id or not user_id:
                    raise RuntimeError(
                        "Weixin login was confirmed without all required credential fields"
                    )
                return WeixinCredentials(
                    bot_token=token,
                    ilink_bot_id=bot_id,
                    base_url=base_url,
                    ilink_user_id=user_id,
                )

            self.log(f"[remote-weixin] unrecognized QR status: {state or 'empty'}")
            self._sleep(1.0)

        raise TimeoutError("Weixin QR login timed out")


__all__ = [
    "WeixinQrAuthenticator",
    "display_qrcode",
]
