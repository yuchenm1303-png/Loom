from __future__ import annotations

from urllib.parse import urlsplit

from .browser_backend_intent import BrowserBackendIntentMixin
from .memory_store import redact_secrets


def _safe_tab_url(value: object) -> str:
    """Expose only a browser tab's origin, never its path/query/fragment/userinfo."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return ""
    if port is not None:
        host = f"{host}:{port}"
    return f"{parsed.scheme}://{host}"


def _safe_current_tab(value: object) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    title = redact_secrets(str(value.get("title") or ""))[:500]
    url = _safe_tab_url(value.get("url"))
    tab_id = str(value.get("tab_id") or "")[:64]
    window_id = str(value.get("window_id") or "")[:64]
    if not any((title, url, tab_id, window_id)):
        return None
    return {
        "title": title,
        "url": url,
        "tab_id": tab_id,
        "window_id": window_id,
    }


class BrowserStatusPrivacyMixin(BrowserBackendIntentMixin):
    """Keep browser discovery/status useful without leaking address-bar secrets.

    The extension needs the full active-tab URL internally to drive the page, but
    backend discovery and runtime status are diagnostic/model-visible surfaces.
    They expose only the tab origin plus a redacted title and opaque tab/window
    identifiers. Page observations still provide Loom's normal redacted URL once
    a browser session is actually opened.

    BrowserBackendIntentMixin also makes a change from current-browser to Loom's
    isolated browser turn-authorized rather than a fallback the model can invent.
    """

    def browser_backend_registry(self):
        rows = super().browser_backend_registry()
        safe_rows: list[dict[str, object]] = []
        for raw in rows:
            row = dict(raw)
            if "current_tab" in row:
                row["current_tab"] = _safe_current_tab(row.get("current_tab"))
            safe_rows.append(row)
        return tuple(safe_rows)

    def browser_status(self, owner_session_id: str | None = None) -> dict[str, object]:
        status = dict(super().browser_status(owner_session_id))
        extension = status.get("extension_bridge")
        if isinstance(extension, dict):
            safe_extension = dict(extension)
            safe_extension["current_tab"] = _safe_current_tab(extension.get("current_tab"))
            status["extension_bridge"] = safe_extension
        backends = status.get("browser_backends")
        if isinstance(backends, list):
            safe_backends: list[dict[str, object]] = []
            for raw in backends:
                if not isinstance(raw, dict):
                    continue
                row = dict(raw)
                if "current_tab" in row:
                    row["current_tab"] = _safe_current_tab(row.get("current_tab"))
                safe_backends.append(row)
            status["browser_backends"] = safe_backends
        return status


__all__ = ["BrowserStatusPrivacyMixin"]
