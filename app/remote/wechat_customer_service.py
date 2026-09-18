from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_QYAPI_BASE = "https://qyapi.weixin.qq.com"
_TOKEN_RETRY_CODES = {40001, 40014, 42001}


class WeChatCustomerServiceError(RuntimeError):
    def __init__(self, message: str, *, errcode: int | None = None) -> None:
        super().__init__(message)
        self.errcode = errcode


@dataclass(frozen=True, slots=True)
class WeChatCustomerServiceConfig:
    corp_id: str
    secret: str
    open_kf_id: str = ""
    timeout_seconds: float = 15.0


@dataclass(frozen=True, slots=True)
class WeChatInboundMessage:
    message_id: str
    external_user_id: str
    open_kf_id: str
    text: str
    send_time: int
    origin: int

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "WeChatInboundMessage | None":
        # sync_msg origin: 3=customer, 4=system event, 5=servicer.
        if int(raw.get("origin") or 0) != 3:
            return None
        if str(raw.get("msgtype") or "") != "text":
            return None
        text_payload = raw.get("text")
        if not isinstance(text_payload, dict):
            return None
        content = str(text_payload.get("content") or "").strip()
        external_user_id = str(raw.get("external_userid") or "").strip()
        open_kf_id = str(raw.get("open_kfid") or "").strip()
        if not content or not external_user_id or not open_kf_id:
            return None
        return cls(
            message_id=str(raw.get("msgid") or "").strip(),
            external_user_id=external_user_id,
            open_kf_id=open_kf_id,
            text=content,
            send_time=int(raw.get("send_time") or 0),
            origin=3,
        )


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = str(text or "").encode("utf-8")
    if len(encoded) <= max_bytes:
        return str(text or "")
    suffix = "\n\n[回复过长，已截断；完整结果请在 Loom 中查看]"
    suffix_bytes = suffix.encode("utf-8")
    budget = max(0, max_bytes - len(suffix_bytes))
    head = encoded[:budget]
    while head:
        try:
            return head.decode("utf-8") + suffix
        except UnicodeDecodeError:
            head = head[:-1]
    return suffix.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


class WeChatCustomerServiceClient:
    """Minimal WeChat Customer Service API client.

    sync_msg can be used without a callback token, with stricter frequency
    limits. Loom uses that mode only as the personal bootstrap transport so a
    first setup does not need a public callback server.
    """

    def __init__(self, config: WeChatCustomerServiceConfig) -> None:
        self.config = config
        self._guard = threading.RLock()
        self._access_token = ""
        self._access_token_expires_at = 0.0

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = _QYAPI_BASE + path
        if query:
            url += "?" + urlencode({key: value for key, value in query.items() if value is not None})
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urlopen(request, timeout=max(1.0, float(self.config.timeout_seconds))) as response:
                payload = response.read()
        except OSError as exc:
            raise WeChatCustomerServiceError(f"WeChat Customer Service request failed: {exc}") from exc
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WeChatCustomerServiceError("WeChat Customer Service returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise WeChatCustomerServiceError("WeChat Customer Service returned a non-object response")
        return decoded

    def _token(self, *, force: bool = False) -> str:
        with self._guard:
            now = time.time()
            if not force and self._access_token and now < self._access_token_expires_at:
                return self._access_token
            payload = self._request_json(
                "GET",
                "/cgi-bin/gettoken",
                query={"corpid": self.config.corp_id, "corpsecret": self.config.secret},
            )
            errcode = int(payload.get("errcode") or 0)
            token = str(payload.get("access_token") or "").strip()
            if errcode != 0 or not token:
                raise WeChatCustomerServiceError(
                    str(payload.get("errmsg") or "failed to obtain access_token"),
                    errcode=errcode,
                )
            expires_in = max(300, int(payload.get("expires_in") or 7200))
            self._access_token = token
            self._access_token_expires_at = now + max(60, expires_in - 120)
            return token

    def _api_request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            token = self._token(force=attempt > 0)
            merged_query = dict(query or {})
            merged_query["access_token"] = token
            payload = self._request_json(method, path, query=merged_query, body=body)
            errcode = int(payload.get("errcode") or 0)
            if errcode == 0:
                return payload
            if attempt == 0 and errcode in _TOKEN_RETRY_CODES:
                continue
            raise WeChatCustomerServiceError(
                str(payload.get("errmsg") or f"WeChat API error {errcode}"),
                errcode=errcode,
            )
        raise WeChatCustomerServiceError("WeChat API request failed after token refresh")

    def list_accounts(self) -> list[dict[str, Any]]:
        accounts: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = self._api_request(
                "GET",
                "/cgi-bin/kf/account/list",
                query={"offset": offset, "limit": 100},
            )
            batch = payload.get("account_list") or []
            rows = [dict(item) for item in batch if isinstance(item, dict)]
            accounts.extend(rows)
            if len(rows) < 100:
                return accounts
            offset += len(rows)

    def contact_url(self, *, open_kf_id: str | None = None, scene: str = "loom-remote") -> str:
        account_id = str(open_kf_id or self.config.open_kf_id).strip()
        if not account_id:
            raise ValueError("open_kf_id must not be empty")
        payload = self._api_request(
            "POST",
            "/cgi-bin/kf/add_contact_way",
            body={"open_kfid": account_id, "scene": str(scene or "loom-remote")[:32]},
        )
        url = str(payload.get("url") or "").strip()
        if not url:
            raise WeChatCustomerServiceError("WeChat did not return a customer-service contact URL")
        return url

    def sync_messages(
        self,
        *,
        cursor: str = "",
        callback_token: str = "",
        limit: int = 100,
        open_kf_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str, bool]:
        account_id = str(open_kf_id or self.config.open_kf_id).strip()
        if not account_id:
            raise ValueError("open_kf_id must not be empty")
        body: dict[str, Any] = {
            "cursor": str(cursor or ""),
            "limit": max(1, min(1000, int(limit))),
            "open_kfid": account_id,
        }
        if callback_token:
            body["token"] = str(callback_token)
        payload = self._api_request("POST", "/cgi-bin/kf/sync_msg", body=body)
        messages = payload.get("msg_list") or []
        if not isinstance(messages, list):
            messages = []
        return (
            [dict(item) for item in messages if isinstance(item, dict)],
            str(payload.get("next_cursor") or cursor or ""),
            bool(int(payload.get("has_more") or 0)),
        )

    def send_text(
        self,
        external_user_id: str,
        text: str,
        *,
        open_kf_id: str | None = None,
    ) -> str:
        recipient = str(external_user_id or "").strip()
        if not recipient:
            raise ValueError("external_user_id must not be empty")
        account_id = str(open_kf_id or self.config.open_kf_id).strip()
        if not account_id:
            raise ValueError("open_kf_id must not be empty")
        content = _truncate_utf8(str(text or "").strip(), 2000)
        if not content:
            raise ValueError("text must not be empty")
        payload = self._api_request(
            "POST",
            "/cgi-bin/kf/send_msg",
            body={
                "touser": recipient,
                "open_kfid": account_id,
                "msgtype": "text",
                "text": {"content": content},
            },
        )
        return str(payload.get("msgid") or "")


__all__ = [
    "WeChatCustomerServiceClient",
    "WeChatCustomerServiceConfig",
    "WeChatCustomerServiceError",
    "WeChatInboundMessage",
]
