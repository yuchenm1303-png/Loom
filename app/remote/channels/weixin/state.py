from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import keyring
from keyring.errors import KeyringError


_STATE_VERSION = 1
_MAX_SEEN_MESSAGE_IDS = 1024
_KEYRING_SERVICE = "loom.remote.weixin"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class WeixinBinding:
    ilink_bot_id: str
    ilink_user_id: str
    base_url: str
    thread_id: str = ""
    bound_at_ms: int = 0
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WeixinBinding":
        return cls(
            ilink_bot_id=str(raw.get("ilinkBotId") or ""),
            ilink_user_id=str(raw.get("ilinkUserId") or ""),
            base_url=str(raw.get("baseUrl") or "https://ilinkai.weixin.qq.com"),
            thread_id=str(raw.get("threadId") or ""),
            bound_at_ms=max(0, int(raw.get("boundAtMs") or 0)),
            created_at=str(raw.get("createdAt") or ""),
            updated_at=str(raw.get("updatedAt") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ilinkBotId": self.ilink_bot_id,
            "ilinkUserId": self.ilink_user_id,
            "baseUrl": self.base_url,
            "threadId": self.thread_id,
            "boundAtMs": self.bound_at_ms,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class WeixinRemoteStateStore:
    """Durable non-secret state for one personal iLink Weixin remote."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "remote" / "weixin.json"
        self._guard = threading.RLock()
        self._data = self._load()

    def _empty(self) -> dict[str, Any]:
        return {
            "version": _STATE_VERSION,
            "getUpdatesBuf": "",
            "historyReady": False,
            "binding": None,
            "seenMessageIds": [],
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._empty()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if not isinstance(raw, dict):
            return self._empty()
        return {
            "version": _STATE_VERSION,
            "getUpdatesBuf": str(raw.get("getUpdatesBuf") or ""),
            "historyReady": bool(raw.get("historyReady")),
            "binding": raw.get("binding") if isinstance(raw.get("binding"), dict) else None,
            "seenMessageIds": [
                str(item)
                for item in (raw.get("seenMessageIds") or [])
                if str(item)
            ][-_MAX_SEEN_MESSAGE_IDS:],
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = json.dumps(self._data, ensure_ascii=False, indent=2) + "\n"
        tmp.write_text(payload, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    @property
    def binding(self) -> WeixinBinding | None:
        with self._guard:
            raw = self._data.get("binding")
            return WeixinBinding.from_dict(dict(raw)) if isinstance(raw, dict) else None

    @property
    def thread_id(self) -> str:
        binding = self.binding
        return binding.thread_id if binding is not None else ""

    @property
    def get_updates_buf(self) -> str:
        with self._guard:
            return str(self._data.get("getUpdatesBuf") or "")

    @property
    def history_ready(self) -> bool:
        with self._guard:
            return bool(self._data.get("historyReady"))

    def bind_login(
        self,
        ilink_bot_id: str,
        ilink_user_id: str,
        base_url: str,
        *,
        bound_at_ms: int,
    ) -> WeixinBinding:
        bot_id = str(ilink_bot_id or "").strip()
        user_id = str(ilink_user_id or "").strip()
        api_base = str(base_url or "").strip() or "https://ilinkai.weixin.qq.com"
        if not bot_id:
            raise ValueError("ilink_bot_id must not be empty")
        if not user_id:
            raise ValueError("ilink_user_id must not be empty")
        now = _utc_now()
        with self._guard:
            current = self.binding
            same_user = current is not None and current.ilink_user_id == user_id
            same_account = same_user and current.ilink_bot_id == bot_id
            binding = WeixinBinding(
                ilink_bot_id=bot_id,
                ilink_user_id=user_id,
                base_url=api_base,
                thread_id=current.thread_id if same_user else "",
                bound_at_ms=max(0, int(bound_at_ms)),
                created_at=current.created_at if same_user else now,
                updated_at=now,
            )
            self._data["binding"] = binding.as_dict()
            if not same_account:
                self._data["getUpdatesBuf"] = ""
                self._data["seenMessageIds"] = []
            self._data["historyReady"] = False
            self._save()
            return binding

    def reset(self) -> None:
        with self._guard:
            self._data = self._empty()
            self._save()

    def set_thread_id(self, thread_id: str) -> None:
        with self._guard:
            current = self.binding
            if current is None:
                raise RuntimeError("cannot set a Loom thread before Weixin login")
            updated = WeixinBinding(
                ilink_bot_id=current.ilink_bot_id,
                ilink_user_id=current.ilink_user_id,
                base_url=current.base_url,
                thread_id=str(thread_id or "").strip(),
                bound_at_ms=current.bound_at_ms,
                created_at=current.created_at,
                updated_at=_utc_now(),
            )
            self._data["binding"] = updated.as_dict()
            self._save()

    def set_get_updates_buf(self, value: str) -> None:
        with self._guard:
            self._data["getUpdatesBuf"] = str(value or "")
            self._save()

    def mark_history_ready(self) -> None:
        with self._guard:
            if not self._data.get("historyReady"):
                self._data["historyReady"] = True
                self._save()

    def accept_message(self, message_id: str) -> bool:
        message_id = str(message_id or "").strip()
        if not message_id:
            return True
        with self._guard:
            seen = list(self._data.get("seenMessageIds") or [])
            if message_id in seen:
                return False
            seen.append(message_id)
            self._data["seenMessageIds"] = seen[-_MAX_SEEN_MESSAGE_IDS:]
            self._save()
            return True


class WeixinCredentialStore:
    """Store iLink bot tokens in the OS keyring, never in Loom JSON state."""

    def _username(self, ilink_bot_id: str) -> str:
        bot_id = str(ilink_bot_id or "").strip()
        if not bot_id:
            raise ValueError("ilink_bot_id must not be empty")
        return f"bot:{bot_id}"

    def get_bot_token(self, ilink_bot_id: str) -> str:
        try:
            return str(
                keyring.get_password(_KEYRING_SERVICE, self._username(ilink_bot_id))
                or ""
            ).strip()
        except KeyringError as exc:
            raise RuntimeError("Loom could not read the Weixin token from the OS keyring") from exc

    def set_bot_token(self, ilink_bot_id: str, token: str) -> None:
        value = str(token or "").strip()
        if not value:
            raise ValueError("bot_token must not be empty")
        try:
            keyring.set_password(_KEYRING_SERVICE, self._username(ilink_bot_id), value)
        except KeyringError as exc:
            raise RuntimeError("Loom could not save the Weixin token to the OS keyring") from exc

    def delete_bot_token(self, ilink_bot_id: str) -> None:
        try:
            keyring.delete_password(_KEYRING_SERVICE, self._username(ilink_bot_id))
        except keyring.errors.PasswordDeleteError:
            return
        except KeyringError as exc:
            raise RuntimeError("Loom could not delete the Weixin token from the OS keyring") from exc


__all__ = [
    "WeixinBinding",
    "WeixinCredentialStore",
    "WeixinRemoteStateStore",
]
