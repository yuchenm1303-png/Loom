from __future__ import annotations

import base64
import json
import os
import re
import socket
import uuid
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen


DEFAULT_ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
CHANNEL_VERSION = "0.1.0"
BOT_AGENT = "Loom/0.1.0"
ILINK_APP_CLIENT_VERSION = (0 << 16) | (1 << 8) | 0

_MESSAGE_TYPE_USER = 1
_MESSAGE_TYPE_BOT = 2
_MESSAGE_STATE_FINISH = 2
_MESSAGE_ITEM_TEXT = 1
_TEXT_CHUNK_MAX_BYTES = 3000
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 15.0
_DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
_STALE_TOKEN_CODE = -14

_SENSITIVE_JSON_RE = re.compile(
    r'("(?:bot_token|context_token|authorization)"\s*:\s*")([^"]*)(")',
    re.I,
)
_BEARER_RE = re.compile(r"Bearer\s+[^\s\"']+", re.I)
_QRCODE_QUERY_RE = re.compile(r"([?&](?:qrcode|verify_code)=)[^&\s]+", re.I)


def redact_sensitive(value: object) -> str:
    text = str(value or "")
    text = _SENSITIVE_JSON_RE.sub(r'\1***\3', text)
    text = _BEARER_RE.sub("Bearer ***", text)
    text = _QRCODE_QUERY_RE.sub(r"\1***", text)
    return text


class WeixinApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        ret: int | None = None,
        errcode: int | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(redact_sensitive(message))
        self.ret = ret
        self.errcode = errcode
        self.http_status = http_status


class WeixinAuthenticationExpired(WeixinApiError):
    pass


@dataclass(frozen=True, slots=True)
class WeixinCredentials:
    bot_token: str
    ilink_bot_id: str
    base_url: str
    ilink_user_id: str


@dataclass(frozen=True, slots=True)
class WeixinQrCode:
    qrcode: str
    image_content: str


@dataclass(frozen=True, slots=True)
class WeixinQrStatus:
    status: str
    bot_token: str = ""
    ilink_bot_id: str = ""
    base_url: str = ""
    ilink_user_id: str = ""
    redirect_host: str = ""


@dataclass(frozen=True, slots=True)
class WeixinInboundMessage:
    message_id: str
    from_user_id: str
    text: str
    context_token: str
    create_time_ms: int
    message_type: int
    item_types: tuple[int, ...]

    @property
    def is_user_text(self) -> bool:
        return (
            self.message_type == _MESSAGE_TYPE_USER
            and bool(self.from_user_id)
            and bool(self.text.strip())
            and _MESSAGE_ITEM_TEXT in self.item_types
        )

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "WeixinInboundMessage":
        raw_items = raw.get("item_list")
        items = raw_items if isinstance(raw_items, list) else []
        item_types: list[int] = []
        text_parts: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                item_type = int(item.get("type") or 0)
            except (TypeError, ValueError):
                item_type = 0
            item_types.append(item_type)
            if item_type != _MESSAGE_ITEM_TEXT:
                continue
            text_item = item.get("text_item")
            if isinstance(text_item, dict):
                value = str(text_item.get("text") or "").strip()
                if value:
                    text_parts.append(value)
        try:
            created = int(raw.get("create_time_ms") or 0)
        except (TypeError, ValueError):
            created = 0
        try:
            message_type = int(raw.get("message_type") or 0)
        except (TypeError, ValueError):
            message_type = 0
        return cls(
            message_id=str(raw.get("message_id") or raw.get("msg_id") or "").strip(),
            from_user_id=str(raw.get("from_user_id") or "").strip(),
            text="\n".join(text_parts).strip(),
            context_token=str(raw.get("context_token") or "").strip(),
            create_time_ms=max(0, created),
            message_type=message_type,
            item_types=tuple(item_types),
        )


@dataclass(frozen=True, slots=True)
class GetUpdatesResponse:
    messages: tuple[WeixinInboundMessage, ...]
    get_updates_buf: str
    longpolling_timeout_ms: int
    ret: int = 0
    errcode: int = 0
    errmsg: str = ""
    timed_out: bool = False


def _split_utf8(text: str, max_bytes: int = _TEXT_CHUNK_MAX_BYTES) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    if len(value.encode("utf-8")) <= max_bytes:
        return [value]

    chunks: list[str] = []
    remaining = value
    while remaining:
        encoded = remaining.encode("utf-8")
        if len(encoded) <= max_bytes:
            chunks.append(remaining)
            break
        head = encoded[:max_bytes]
        while head:
            try:
                candidate = head.decode("utf-8")
                break
            except UnicodeDecodeError:
                head = head[:-1]
        if not head:
            raise ValueError("max_bytes is too small for UTF-8 text")
        split_at = max(candidate.rfind("\n"), candidate.rfind("。"), candidate.rfind(" "))
        if split_at >= max(1, len(candidate) // 3):
            candidate = candidate[: split_at + 1].rstrip()
        if not candidate:
            candidate = head.decode("utf-8")
        chunks.append(candidate)
        remaining = remaining[len(candidate) :].lstrip()
    return chunks


def _random_wechat_uin() -> str:
    uint32 = int.from_bytes(os.urandom(4), "big", signed=False)
    return base64.b64encode(str(uint32).encode("utf-8")).decode("ascii")


def _base_info() -> dict[str, str]:
    return {
        "channel_version": CHANNEL_VERSION,
        "bot_agent": BOT_AGENT,
    }


class WeixinApiClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_ILINK_BASE_URL,
        timeout_seconds: float = _DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = str(base_url or DEFAULT_ILINK_BASE_URL).rstrip("/")
        self.timeout_seconds = max(1.0, float(timeout_seconds))

    def _common_headers(self) -> dict[str, str]:
        return {
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
        }

    def _post_headers(self, token: str = "") -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": _random_wechat_uin(),
            **self._common_headers(),
        }
        if str(token or "").strip():
            headers["Authorization"] = f"Bearer {str(token).strip()}"
        return headers

    def _url(self, path: str, *, base_url: str | None = None) -> str:
        root = str(base_url or self.base_url or DEFAULT_ILINK_BASE_URL).rstrip("/") + "/"
        return urljoin(root, str(path or "").lstrip("/"))

    def _decode_response(
        self,
        payload: bytes,
        *,
        token_was_used: bool,
        http_status: int | None = None,
    ) -> dict[str, Any]:
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WeixinApiError(
                "iLink returned invalid JSON", http_status=http_status
            ) from exc
        if not isinstance(value, dict):
            raise WeixinApiError(
                "iLink returned a non-object response", http_status=http_status
            )
        if token_was_used:
            ret = _as_int(value.get("ret"))
            errcode = _as_int(value.get("errcode"))
            if ret == _STALE_TOKEN_CODE or errcode == _STALE_TOKEN_CODE:
                raise WeixinAuthenticationExpired(
                    "iLink login expired",
                    ret=ret,
                    errcode=errcode,
                    http_status=http_status,
                )
        return value

    def _request(
        self,
        method: str,
        path: str,
        *,
        base_url: str | None = None,
        token: str = "",
        body: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        common_headers_only: bool = False,
    ) -> dict[str, Any]:
        data = None
        headers = self._common_headers() if common_headers_only else self._post_headers(token)
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = Request(
            self._url(path, base_url=base_url),
            data=data,
            headers=headers,
            method=method.upper(),
        )
        timeout = self.timeout_seconds if timeout_seconds is None else max(1.0, timeout_seconds)
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read()
                status = getattr(response, "status", None)
        except HTTPError as exc:
            if token and exc.code in {401, 403}:
                raise WeixinAuthenticationExpired(
                    "iLink rejected the saved login",
                    http_status=exc.code,
                ) from exc
            raise WeixinApiError(
                f"iLink HTTP request failed with status {exc.code}",
                http_status=exc.code,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise TimeoutError("iLink request timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise TimeoutError("iLink request timed out") from exc
            raise WeixinApiError(
                f"iLink network request failed: {type(exc.reason).__name__}"
            ) from exc
        except OSError as exc:
            raise WeixinApiError(
                f"iLink network request failed: {type(exc).__name__}"
            ) from exc
        return self._decode_response(
            payload,
            token_was_used=bool(token),
            http_status=status,
        )

    def get_bot_qrcode(
        self,
        *,
        local_token_list: Iterable[str] = (),
        bot_type: str = "3",
    ) -> WeixinQrCode:
        tokens = [str(item).strip() for item in local_token_list if str(item).strip()][-10:]
        payload = self._request(
            "POST",
            f"/ilink/bot/get_bot_qrcode?{urlencode({'bot_type': str(bot_type or '3')})}",
            body={"local_token_list": tokens},
        )
        qrcode = str(payload.get("qrcode") or "").strip()
        image_content = str(payload.get("qrcode_img_content") or "").strip()
        if not qrcode or not image_content:
            raise WeixinApiError("iLink QR response is missing required fields")
        return WeixinQrCode(qrcode=qrcode, image_content=image_content)

    def get_qrcode_status(
        self,
        qrcode: str,
        *,
        base_url: str = DEFAULT_ILINK_BASE_URL,
        verify_code: str = "",
        timeout_seconds: float = 35.0,
    ) -> WeixinQrStatus:
        query = {"qrcode": str(qrcode or "")}
        if verify_code:
            query["verify_code"] = str(verify_code)
        try:
            payload = self._request(
                "GET",
                f"/ilink/bot/get_qrcode_status?{urlencode(query, quote_via=quote)}",
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                common_headers_only=True,
            )
        except TimeoutError:
            return WeixinQrStatus(status="wait")
        return WeixinQrStatus(
            status=str(payload.get("status") or "wait").strip(),
            bot_token=str(payload.get("bot_token") or "").strip(),
            ilink_bot_id=str(payload.get("ilink_bot_id") or "").strip(),
            base_url=str(payload.get("baseurl") or "").strip(),
            ilink_user_id=str(payload.get("ilink_user_id") or "").strip(),
            redirect_host=str(payload.get("redirect_host") or "").strip(),
        )

    def get_updates(
        self,
        credentials: WeixinCredentials,
        *,
        get_updates_buf: str = "",
        timeout_ms: int = _DEFAULT_LONG_POLL_TIMEOUT_MS,
    ) -> GetUpdatesResponse:
        requested_timeout_ms = max(1000, int(timeout_ms or _DEFAULT_LONG_POLL_TIMEOUT_MS))
        try:
            payload = self._request(
                "POST",
                "/ilink/bot/getupdates",
                base_url=credentials.base_url,
                token=credentials.bot_token,
                body={
                    "get_updates_buf": str(get_updates_buf or ""),
                    "base_info": _base_info(),
                },
                timeout_seconds=(requested_timeout_ms / 1000.0) + 2.0,
            )
        except TimeoutError:
            return GetUpdatesResponse(
                messages=(),
                get_updates_buf=str(get_updates_buf or ""),
                longpolling_timeout_ms=requested_timeout_ms,
                timed_out=True,
            )
        ret = _as_int(payload.get("ret"))
        errcode = _as_int(payload.get("errcode"))
        errmsg = str(payload.get("errmsg") or "")
        if ret == _STALE_TOKEN_CODE or errcode == _STALE_TOKEN_CODE:
            raise WeixinAuthenticationExpired(
                "iLink login expired", ret=ret, errcode=errcode
            )
        if (ret not in {None, 0}) or (errcode not in {None, 0}):
            raise WeixinApiError(
                "iLink getupdates returned an error",
                ret=ret,
                errcode=errcode,
            )
        raw_messages = payload.get("msgs")
        messages = tuple(
            WeixinInboundMessage.from_api(dict(item))
            for item in (raw_messages if isinstance(raw_messages, list) else [])
            if isinstance(item, dict)
        )
        next_timeout = _as_int(payload.get("longpolling_timeout_ms"))
        return GetUpdatesResponse(
            messages=messages,
            get_updates_buf=str(payload.get("get_updates_buf") or get_updates_buf or ""),
            longpolling_timeout_ms=(
                next_timeout if next_timeout is not None and next_timeout > 0 else requested_timeout_ms
            ),
            ret=ret or 0,
            errcode=errcode or 0,
            errmsg=errmsg,
        )

    def send_text(
        self,
        credentials: WeixinCredentials,
        *,
        to_user_id: str,
        context_token: str,
        text: str,
    ) -> tuple[str, ...]:
        recipient = str(to_user_id or "").strip()
        context = str(context_token or "").strip()
        if not recipient:
            raise ValueError("to_user_id must not be empty")
        if not context:
            raise ValueError("context_token must not be empty")
        chunks = _split_utf8(text)
        client_ids: list[str] = []
        for chunk in chunks:
            client_id = f"loom-{uuid.uuid4()}"
            payload = self._request(
                "POST",
                "/ilink/bot/sendmessage",
                base_url=credentials.base_url,
                token=credentials.bot_token,
                body={
                    "msg": {
                        "from_user_id": "",
                        "to_user_id": recipient,
                        "client_id": client_id,
                        "message_type": _MESSAGE_TYPE_BOT,
                        "message_state": _MESSAGE_STATE_FINISH,
                        "context_token": context,
                        "item_list": [
                            {
                                "type": _MESSAGE_ITEM_TEXT,
                                "text_item": {"text": chunk},
                            }
                        ],
                    },
                    "base_info": _base_info(),
                },
            )
            ret = _as_int(payload.get("ret"))
            errcode = _as_int(payload.get("errcode"))
            if ret == _STALE_TOKEN_CODE or errcode == _STALE_TOKEN_CODE:
                raise WeixinAuthenticationExpired(
                    "iLink login expired", ret=ret, errcode=errcode
                )
            if ret not in {None, 0}:
                raise WeixinApiError(
                    "iLink sendmessage returned an error",
                    ret=ret,
                    errcode=errcode,
                )
            client_ids.append(client_id)
        return tuple(client_ids)

    def notify_start(self, credentials: WeixinCredentials) -> None:
        self._notify(credentials, "/ilink/bot/msg/notifystart")

    def notify_stop(self, credentials: WeixinCredentials) -> None:
        self._notify(credentials, "/ilink/bot/msg/notifystop")

    def _notify(self, credentials: WeixinCredentials, path: str) -> None:
        payload = self._request(
            "POST",
            path,
            base_url=credentials.base_url,
            token=credentials.bot_token,
            body={"base_info": _base_info()},
            timeout_seconds=10.0,
        )
        ret = _as_int(payload.get("ret"))
        errcode = _as_int(payload.get("errcode"))
        if ret == _STALE_TOKEN_CODE or errcode == _STALE_TOKEN_CODE:
            raise WeixinAuthenticationExpired(
                "iLink login expired", ret=ret, errcode=errcode
            )
        if ret not in {None, 0}:
            raise WeixinApiError("iLink notify returned an error", ret=ret, errcode=errcode)


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "BOT_AGENT",
    "CHANNEL_VERSION",
    "DEFAULT_ILINK_BASE_URL",
    "GetUpdatesResponse",
    "ILINK_APP_CLIENT_VERSION",
    "ILINK_APP_ID",
    "WeixinApiClient",
    "WeixinApiError",
    "WeixinAuthenticationExpired",
    "WeixinCredentials",
    "WeixinInboundMessage",
    "WeixinQrCode",
    "WeixinQrStatus",
    "redact_sensitive",
]
