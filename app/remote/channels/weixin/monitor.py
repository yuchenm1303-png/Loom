from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .api import (
    GetUpdatesResponse,
    WeixinApiClient,
    WeixinApiError,
    WeixinAuthenticationExpired,
    WeixinCredentials,
    WeixinInboundMessage,
)
from .state import WeixinRemoteStateStore


LogListener = Callable[[str], None]
MessageHandler = Callable[[WeixinInboundMessage], None]


class WeixinMonitor:
    def __init__(
        self,
        *,
        api: WeixinApiClient,
        state: WeixinRemoteStateStore,
        credentials: WeixinCredentials,
        on_message: MessageHandler,
        log: LogListener | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api = api
        self.state = state
        self.credentials = credentials
        self.on_message = on_message
        self.log = log or (lambda _message: None)
        self._sleep = sleep
        self._stop = threading.Event()
        self._next_timeout_ms = 35_000

    def stop(self) -> None:
        self._stop.set()

    def poll_once(self) -> GetUpdatesResponse:
        binding = self.state.binding
        if binding is None:
            raise RuntimeError("Weixin monitor cannot run before QR binding")
        response = self.api.get_updates(
            self.credentials,
            get_updates_buf=self.state.get_updates_buf,
            timeout_ms=self._next_timeout_ms,
        )
        if response.longpolling_timeout_ms > 0:
            self._next_timeout_ms = response.longpolling_timeout_ms

        # The cursor is committed before dispatching any message from this batch.
        # A crash may require the user to resend a command, but it must never
        # replay a half-executed Loom turn after restart (at-most-once ingress).
        if (
            response.get_updates_buf
            and response.get_updates_buf != self.state.get_updates_buf
        ):
            self.state.set_get_updates_buf(response.get_updates_buf)

        first_history_poll = not self.state.history_ready
        for message in response.messages:
            if message.message_type != 1:
                continue
            if message.from_user_id != binding.ilink_user_id:
                self.log("[remote-weixin] ignored message from an unbound Weixin user")
                continue
            if (
                binding.bound_at_ms
                and message.create_time_ms
                and message.create_time_ms <= binding.bound_at_ms
            ):
                self.log("[remote-weixin] ignored a message older than the QR binding")
                continue
            if first_history_poll and not message.create_time_ms:
                self.log("[remote-weixin] ignored undated history during the binding boundary")
                continue
            if not message.is_user_text:
                if message.item_types:
                    self.log("[remote-weixin] ignored unsupported non-text message")
                continue
            if not self.state.accept_message(message.message_id):
                self.log("[remote-weixin] ignored duplicate message")
                continue
            self.on_message(message)

        if first_history_poll and not response.timed_out:
            self.state.mark_history_ready()
        return response

    def run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                self.poll_once()
                failures = 0
            except WeixinAuthenticationExpired:
                raise
            except WeixinApiError as exc:
                failures += 1
                self.log(
                    f"[remote-weixin] getupdates failed: {type(exc).__name__} "
                    f"({failures}/3)"
                )
                delay = 30.0 if failures >= 3 else 2.0
                if failures >= 3:
                    failures = 0
                self._sleep(delay)
            except TimeoutError:
                # Client-side long-poll timeouts are normal; immediately poll again.
                failures = 0


__all__ = ["WeixinMonitor"]
