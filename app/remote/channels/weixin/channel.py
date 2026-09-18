from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.remote.base import RemoteMessage, sanitize_remote_text

from .api import WeixinApiClient, WeixinCredentials, WeixinInboundMessage
from .state import WeixinRemoteStateStore

if TYPE_CHECKING:
    from app.remote.service import LoomRemoteService


LogListener = Callable[[str], None]


class WeixinChannel:
    """Authenticated personal-Weixin text channel backed by Tencent iLink."""

    def __init__(
        self,
        *,
        api: WeixinApiClient,
        credentials: WeixinCredentials,
        state: WeixinRemoteStateStore,
        log: LogListener | None = None,
    ) -> None:
        self.api = api
        self.state = state
        self.log = log or (lambda _message: None)
        self._guard = threading.RLock()
        self._credentials = credentials
        self._context_token = ""
        self._recipient_id = ""
        self._service: LoomRemoteService | None = None

    def attach_service(self, service: "LoomRemoteService") -> None:
        self._service = service

    def update_credentials(self, credentials: WeixinCredentials) -> None:
        with self._guard:
            self._credentials = credentials
            self._context_token = ""
            self._recipient_id = ""

    @property
    def credentials(self) -> WeixinCredentials:
        with self._guard:
            return self._credentials

    def handle_inbound(self, message: WeixinInboundMessage) -> None:
        service = self._service
        if service is None:
            raise RuntimeError("WeixinChannel is not attached to LoomRemoteService")
        context = message.context_token.strip()
        if not context:
            self.log("[remote-weixin] ignored text message without context token")
            return
        with self._guard:
            self._context_token = context
            self._recipient_id = message.from_user_id
        service.handle_message(
            RemoteMessage(
                message_id=message.message_id,
                sender_id=message.from_user_id,
                text=message.text,
                created_at_ms=message.create_time_ms,
            )
        )

    def send_text(self, text: str) -> None:
        clean = sanitize_remote_text(text)
        if not clean:
            return
        binding = self.state.binding
        if binding is None:
            raise RuntimeError("Weixin remote is not bound")
        with self._guard:
            credentials = self._credentials
            context_token = self._context_token
            recipient_id = self._recipient_id or binding.ilink_user_id
        if recipient_id != binding.ilink_user_id:
            raise RuntimeError("refusing to reply to an unbound Weixin user")
        if not context_token:
            raise RuntimeError("no live Weixin context token is available for reply")
        self.api.send_text(
            credentials,
            to_user_id=recipient_id,
            context_token=context_token,
            text=clean,
        )

    def send_progress(self, text: str) -> None:
        """Reserved progress-message surface for future non-streaming updates."""
        self.send_text(text)


__all__ = ["WeixinChannel"]
