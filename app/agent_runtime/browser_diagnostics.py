from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .memory_store import redact_secrets


_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "cookie",
    "password",
    "passwd",
    "refresh_token",
    "secret",
    "session",
    "set-cookie",
    "token",
)
_SENSITIVE_QUERY_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:api[_-]?key|token|secret|password|passwd|cookie|authorization|auth|signature|session)(?:$|[_-])"
)
_URL_KEY_NAMES = {"url", "href", "src", "action_url", "current_url", "target_url"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _default_log_root() -> Path:
    configured = os.environ.get("LOOM_BROWSER_LOG_DIR") or os.environ.get("LOOM_BROWSER_DIAG_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.cwd() / ".loom" / "logs" / "browser-use").resolve()


def _normalized_key(key: str) -> str:
    return str(key or "").casefold().replace("-", "_")


def _key_is_sensitive(key: str) -> bool:
    lowered = _normalized_key(key)
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _key_is_url(key: str) -> bool:
    lowered = _normalized_key(key)
    return lowered in _URL_KEY_NAMES or lowered.endswith("_url") or lowered.endswith("_href")


def _redact_url(value: str) -> str:
    raw = redact_secrets(str(value or ""))
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    if not parsed.scheme or not parsed.netloc:
        return raw
    pairs: list[tuple[str, str]] = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        pairs.append((key, "[REDACTED]" if _SENSITIVE_QUERY_KEY.search(key) else redact_secrets(item)))
    fragment = redact_secrets(parsed.fragment)
    if any(term in fragment.casefold() for term in ("access_token", "refresh_token", "id_token", "api_key", "token=")):
        fragment = "[REDACTED]"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs, doseq=True), fragment))


def _safe_value(value: Any, *, key: str = "", max_string: int = 4000, depth: int = 0) -> Any:
    if _key_is_sensitive(key):
        return "[REDACTED]"
    if depth > 6:
        return "[TRUNCATED_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = _redact_url(value) if _key_is_url(key) else redact_secrets(value)
        if len(text) > max_string:
            return text[:max_string] + "...[truncated]"
        return text
    if isinstance(value, bytes):
        return {"bytes": len(value)}
    if isinstance(value, dict):
        return {
            str(item_key)[:160]: _safe_value(item_value, key=str(item_key), max_string=max_string, depth=depth + 1)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        clipped = items[:120]
        payload = [_safe_value(item, max_string=max_string, depth=depth + 1) for item in clipped]
        if len(items) > len(clipped):
            payload.append({"truncated_items": len(items) - len(clipped)})
        return payload
    return _safe_value(str(value), max_string=max_string, depth=depth + 1)


def summarize_browser_state_payload(
    payload: dict[str, Any] | None,
    *,
    include_dom_excerpt: bool = True,
) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    tabs = data.get("tabs") if isinstance(data.get("tabs"), list) else []
    page_info = data.get("page_info") if isinstance(data.get("page_info"), dict) else {}
    errors = data.get("errors") if isinstance(data.get("errors"), list) else []
    dom = str(data.get("dom") or "")
    return {
        "url": _safe_value(data.get("url"), key="url", max_string=2000),
        "title": _safe_value(data.get("title"), key="title", max_string=1000),
        "tab_id": _safe_value(page_info.get("tab_id") or data.get("tab_id"), key="tab_id", max_string=128),
        "tab_count": len(tabs),
        "dom_chars": len(dom),
        "dom_excerpt": _safe_value(dom[:1600], key="dom_excerpt", max_string=1800)
        if include_dom_excerpt
        else "[omitted after browser text input]",
        "errors": _safe_value(errors, key="errors", max_string=1600),
        "page_info": _safe_value(page_info, key="page_info", max_string=1200),
    }


def summarize_bridge_args(action: str, args: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(args or {})
    if action in {"type_text", "send_text"} and "text" in data:
        text = str(data.pop("text") or "")
        data["text_length"] = len(text)
        data["text_present"] = bool(text)
    return _safe_value(data, max_string=1200)


class BrowserDiagnosticLog:
    """Append-only JSONL diagnostics for browser automation smoke tests.

    The log is local-only and intentionally redacts secret-shaped fields. It is
    detailed enough to reconstruct the browser bridge/browser-use lifecycle
    without storing screenshot bytes or typed text payloads.
    """

    def __init__(self, *, root: str | Path | None = None, enabled: bool = True) -> None:
        self.root = Path(root).expanduser().resolve() if root is not None else _default_log_root()
        self.enabled = bool(enabled)
        self.run_id = f"browser-{_timestamp_slug()}-{os.getpid()}"
        self.started_at = _utc_now()
        self._lock = threading.RLock()
        self._seq = 0
        self._file = self.root / f"{self.run_id}.jsonl"

    @classmethod
    def from_environment(cls) -> "BrowserDiagnosticLog":
        raw = str(os.environ.get("LOOM_BROWSER_DIAGNOSTICS") or os.environ.get("LOOM_BROWSER_LOGS") or "1")
        enabled = raw.strip().casefold() not in {"0", "false", "off", "no", "disabled"}
        return cls(enabled=enabled)

    @property
    def path(self) -> Path:
        return self._file

    def status(self, *, expose_path: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "enabled": self.enabled,
            "run_id": self.run_id,
            "entries": self._seq,
            "started_at": self.started_at,
        }
        if expose_path:
            payload["log_dir"] = str(self.root)
            payload["log_file"] = str(self._file)
        return payload

    def event(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._seq += 1
            record = {
                "ts": _utc_now(),
                "mono_ms": int(time.monotonic() * 1000),
                "seq": self._seq,
                "run_id": self.run_id,
                "event": str(event or "browser.event"),
                **_safe_value(fields, max_string=5000),
            }
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                with self._file.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            except Exception:
                # Diagnostics must never break browser automation itself.
                return


__all__ = [
    "BrowserDiagnosticLog",
    "summarize_bridge_args",
    "summarize_browser_state_payload",
]
