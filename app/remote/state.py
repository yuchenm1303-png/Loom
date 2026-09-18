from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_STATE_VERSION = 1
_MAX_SEEN_MESSAGE_IDS = 512


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class WeChatRemoteBinding:
    external_user_id: str
    open_kf_id: str
    thread_id: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WeChatRemoteBinding":
        return cls(
            external_user_id=str(raw.get("externalUserId") or ""),
            open_kf_id=str(raw.get("openKfId") or ""),
            thread_id=str(raw.get("threadId") or ""),
            created_at=str(raw.get("createdAt") or ""),
            updated_at=str(raw.get("updatedAt") or ""),
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "externalUserId": self.external_user_id,
            "openKfId": self.open_kf_id,
            "threadId": self.thread_id,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


class WeChatRemoteStateStore:
    """Small durable state store for one personal WeChat remote binding.

    The store intentionally contains no API secret or access token. Those stay in
    environment variables / process configuration, while this file only keeps the
    remote user's opaque WeChat id, the bound Loom thread, the sync cursor and a
    bounded duplicate-suppression window.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "remote" / "wechat-customer-service.json"
        self._guard = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "version": _STATE_VERSION,
                "cursor": "",
                "binding": None,
                "seenMessageIds": [],
            }
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "version": _STATE_VERSION,
                "cursor": "",
                "binding": None,
                "seenMessageIds": [],
            }
        if not isinstance(raw, dict):
            raw = {}
        return {
            "version": _STATE_VERSION,
            "cursor": str(raw.get("cursor") or ""),
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
    def cursor(self) -> str:
        with self._guard:
            return str(self._data.get("cursor") or "")

    @property
    def binding(self) -> WeChatRemoteBinding | None:
        with self._guard:
            raw = self._data.get("binding")
            return WeChatRemoteBinding.from_dict(dict(raw)) if isinstance(raw, dict) else None

    def bind(self, external_user_id: str, open_kf_id: str) -> WeChatRemoteBinding:
        external_user_id = str(external_user_id or "").strip()
        open_kf_id = str(open_kf_id or "").strip()
        if not external_user_id:
            raise ValueError("external_user_id must not be empty")
        if not open_kf_id:
            raise ValueError("open_kf_id must not be empty")
        now = _utc_now()
        with self._guard:
            current = self.binding
            binding = WeChatRemoteBinding(
                external_user_id=external_user_id,
                open_kf_id=open_kf_id,
                thread_id=current.thread_id if current and current.external_user_id == external_user_id else "",
                created_at=current.created_at if current and current.external_user_id == external_user_id else now,
                updated_at=now,
            )
            self._data["binding"] = binding.as_dict()
            self._save()
            return binding

    def reset_binding(self) -> None:
        with self._guard:
            self._data["binding"] = None
            self._data["cursor"] = ""
            self._data["seenMessageIds"] = []
            self._save()

    def set_thread_id(self, thread_id: str) -> WeChatRemoteBinding:
        thread_id = str(thread_id or "").strip()
        with self._guard:
            current = self.binding
            if current is None:
                raise RuntimeError("cannot bind a Loom thread before the WeChat user is paired")
            updated = WeChatRemoteBinding(
                external_user_id=current.external_user_id,
                open_kf_id=current.open_kf_id,
                thread_id=thread_id,
                created_at=current.created_at,
                updated_at=_utc_now(),
            )
            self._data["binding"] = updated.as_dict()
            self._save()
            return updated

    def set_cursor(self, cursor: str) -> None:
        with self._guard:
            self._data["cursor"] = str(cursor or "")
            self._save()

    def accept_message(self, message_id: str) -> bool:
        """Return True once for a message id and persist the decision before dispatch.

        This is at-most-once ingress. If the process dies after accepting a message
        but before executing it, the user can resend the command; avoiding duplicate
        Agent turns after a restart is more important than automatically replaying a
        possibly half-executed task.
        """

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


__all__ = [
    "WeChatRemoteBinding",
    "WeChatRemoteStateStore",
]
