from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.ai import ModelResponse, ToolCall

from .browser_backend import browser_use_session_backend_factory
from .browser_security import BrowserSecurityPolicy
from .browser_session import (
    BrowserBackendFactory,
    BrowserLaunchOptions,
    BrowserPageState,
    BrowserSessionManager,
    ManagedBrowserSession,
)
from .browser_use_backend import browser_use_available
from .memory_store import redact_secrets
from .web_search_runtime import WebSearchRuntime


_SENSITIVE_QUERY_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:api[_-]?key|token|secret|password|passwd|cookie|authorization|auth|signature|session)(?:$|[_-])"
)
_BROWSER_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(cookie|set-cookie|authorization|password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key)"
    r"\s*([:=])\s*([^\s,;<>]+)"
)
_JWT_RE = re.compile(r"\b[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}\b")
_PROVIDER_TOKEN_RE = re.compile(
    r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{12,})\b"
)
_BLOCKED_SECRET_ARGUMENT = "_loom_blocked_sensitive_input"


def redact_browser_text(value: str) -> str:
    text = redact_secrets(str(value or ""))
    text = _JWT_RE.sub("[REDACTED_TOKEN]", text)
    text = _PROVIDER_TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    return _BROWSER_SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        text,
    )


def redact_browser_url(value: str) -> str:
    raw = redact_browser_text(str(value or ""))
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    if not parsed.scheme or not parsed.netloc:
        return raw
    pairs: list[tuple[str, str]] = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        pairs.append((key, "[REDACTED]" if _SENSITIVE_QUERY_KEY.search(key) else redact_browser_text(item)))
    fragment = redact_browser_text(parsed.fragment)
    if any(term in fragment.casefold() for term in ("access_token", "refresh_token", "id_token", "api_key=")):
        fragment = "[REDACTED]"
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs, doseq=True), fragment))


def _safe_state_dict(state: BrowserPageState, *, max_dom_chars: int = 30_000) -> dict[str, object]:
    payload = state.to_dict(max_dom_chars=max_dom_chars)
    payload["url"] = redact_browser_url(str(payload.get("url", "")))
    payload["title"] = redact_browser_text(str(payload.get("title", "")))
    payload["dom"] = redact_browser_text(str(payload.get("dom", "")))
    safe_tabs: list[dict[str, str]] = []
    for tab in payload.get("tabs", []) or []:
        if not isinstance(tab, dict):
            continue
        safe_tabs.append(
            {
                "tab_id": str(tab.get("tab_id", ""))[:64],
                "url": redact_browser_url(str(tab.get("url", "")))[:4000],
                "title": redact_browser_text(str(tab.get("title", "")))[:1000],
            }
        )
    payload["tabs"] = safe_tabs
    payload["errors"] = [redact_browser_text(str(item))[:2000] for item in payload.get("errors", []) or []]
    return payload


def _sanitize_browser_tool_call(call: ToolCall) -> ToolCall:
    """Remove secret-shaped browser arguments before Runtime can persist them.

    Runtime v2 durably records model tool-call arguments before tool execution. A
    browser password/token therefore cannot be made safe merely by redacting the
    ToolResult. Browser v1 has no secret-handle channel, so secret-shaped values are
    replaced *before* the canonical Session/event boundary and the call is marked
    invalid. The extra marker is intentionally outside the tool schema; validation
    rejects it before any browser action can execute with a redacted credential.
    """

    if not call.name.startswith("browser_"):
        return call
    arguments: dict[str, Any] = dict(call.arguments)
    blocked = False

    if call.name == "browser_type" and "text" in arguments:
        raw_text = str(arguments.get("text") or "")
        safe_text = redact_browser_text(raw_text)
        if safe_text != raw_text:
            arguments["text"] = "[REDACTED_SENSITIVE_INPUT]"
            blocked = True

    if call.name in {"browser_open", "browser_navigate"} and "url" in arguments:
        raw_url = str(arguments.get("url") or "")
        safe_url = redact_browser_url(raw_url)
        if safe_url != raw_url:
            arguments["url"] = safe_url
            blocked = True

    if blocked:
        arguments[_BLOCKED_SECRET_ARGUMENT] = True
        return ToolCall(call_id=call.call_id, name=call.name, arguments=arguments)
    return call


class _BrowserSecretBoundaryPlatform:
    """Small platform adapter that scrubs browser calls before durable Runtime sees them."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate

    def execute_chat(self, profile_id, request) -> ModelResponse:
        response = self._delegate.execute_chat(profile_id, request)
        if not isinstance(response, ModelResponse) or not response.tool_calls:
            return response
        calls = tuple(_sanitize_browser_tool_call(call) for call in response.tool_calls)
        if calls == response.tool_calls:
            return response
        return ModelResponse(
            text=response.text,
            tool_calls=calls,
            usage=response.usage,
            finish_reason=response.finish_reason,
            response_id=response.response_id,
        )


@dataclass(frozen=True, slots=True)
class BrowserStateSnapshot:
    browser_id: str
    state_revision: int
    state: BrowserPageState

    def to_dict(self, *, max_dom_chars: int = 30_000) -> dict[str, object]:
        return {
            "browser_id": self.browser_id,
            "state_revision": self.state_revision,
            **_safe_state_dict(self.state, max_dom_chars=max_dom_chars),
        }


BrowserSessionHandle = ManagedBrowserSession


class BrowserSessionStore(BrowserSessionManager):
    """Ephemeral Loom-session-owned browser handles.

    Browser processes, tabs, selector maps and cookies are deliberately not written
    to Loom's durable Session/SQLite state. After a Loom process restart there is no
    automatic browser reattachment in v1; callers must open a new browser session.
    """

    def _validated_state(self, state: BrowserPageState, options: BrowserLaunchOptions) -> BrowserPageState:
        checked = super()._validated_state(state, options)
        for tab in checked.tabs:
            url = str(tab.get("url", "")) if isinstance(tab, dict) else ""
            if url and url != "about:blank":
                self.url_policy.validate(url, allowed_domains=options.allowed_domains)
        return checked

    def snapshot(self, owner_session_id: str, browser_id: str, *, refresh: bool = False) -> BrowserStateSnapshot:
        item = self._owned(owner_session_id, browser_id)
        if refresh:
            self.state(owner_session_id, browser_id)
            item = self._owned(owner_session_id, browser_id)
        return BrowserStateSnapshot(
            browser_id=item.browser_id,
            state_revision=self._revision(item),
            state=item.last_state,
        )

    def ensure_revision(self, owner_session_id: str, browser_id: str, expected_revision: int) -> None:
        item = self._owned(owner_session_id, browser_id)
        current = self._revision(item)
        expected = int(expected_revision)
        if expected != current:
            raise RuntimeError(
                f"stale browser state_revision {expected}; latest is {current}. "
                "Call browser_state and retry with the latest element index."
            )

    def refresh(self, owner_session_id: str, browser_id: str) -> BrowserStateSnapshot:
        item = self._owned(owner_session_id, browser_id)
        method = getattr(item.backend, "refresh", None)
        if not callable(method):
            raise RuntimeError("browser backend does not support refresh")
        self._update_state(item, method())
        return self.snapshot(owner_session_id, browser_id)

    def tabs(self, owner_session_id: str, browser_id: str) -> BrowserStateSnapshot:
        item = self._owned(owner_session_id, browser_id)
        method = getattr(item.backend, "tabs", None)
        state = method() if callable(method) else item.backend.state()
        self._update_state(item, state)
        return self.snapshot(owner_session_id, browser_id)

    def switch_tab(self, owner_session_id: str, browser_id: str, tab_id: str) -> BrowserStateSnapshot:
        item = self._owned(owner_session_id, browser_id)
        method = getattr(item.backend, "switch_tab", None)
        if not callable(method):
            raise RuntimeError("browser backend does not support tab switching")
        self._update_state(item, method(str(tab_id)))
        return self.snapshot(owner_session_id, browser_id)

    def close_tab(self, owner_session_id: str, browser_id: str, tab_id: str) -> BrowserStateSnapshot:
        item = self._owned(owner_session_id, browser_id)
        method = getattr(item.backend, "close_tab", None)
        if not callable(method):
            raise RuntimeError("browser backend does not support tab closing")
        self._update_state(item, method(str(tab_id)))
        return self.snapshot(owner_session_id, browser_id)

    @staticmethod
    def _revision(item: ManagedBrowserSession) -> int:
        return max(0, int(getattr(item.backend, "state_revision", 0)))


class BrowserRuntime(WebSearchRuntime):
    """Runtime v2 Browser layer with Loom-owned policy, lifecycle and tools."""

    def __init__(
        self,
        *args,
        browser_backend_factory: BrowserBackendFactory | None = None,
        browser_security_policy: BrowserSecurityPolicy | None = None,
        auto_configure_browser: bool = True,
        browser_headless: bool = True,
        browser_allowed_domains: Sequence[str] = (),
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        # The base Runtime persists ModelResponse tool arguments immediately. Put
        # browser-specific secret scrubbing in front of that durable boundary.
        self.platform = _BrowserSecretBoundaryPlatform(self.platform)

        factory = browser_backend_factory
        backend_name = "custom" if factory is not None else "disabled"
        if factory is None and auto_configure_browser and browser_use_available():
            factory = browser_use_session_backend_factory
            backend_name = "browser-use"

        self.browser_backend_name = backend_name
        self.browser_headless = bool(browser_headless)
        self.browser_allowed_domains = tuple(str(item) for item in browser_allowed_domains)
        self.browser_security_policy = browser_security_policy or BrowserSecurityPolicy()
        self.browser_sessions = (
            BrowserSessionStore(factory, url_policy=self.browser_security_policy)
            if factory is not None
            else None
        )

        from .browser_tools import browser_tools

        for tool in browser_tools(self):
            if self.tools.get(tool.name) is None:
                self.tools.register(tool)

    def browser_status(self, owner_session_id: str | None = None) -> dict[str, object]:
        store = self.browser_sessions
        active = 0
        if store is not None and owner_session_id:
            active = len(store.list(owner_session_id))
        return {
            "enabled": store is not None,
            "backend": self.browser_backend_name,
            "active_sessions": active,
            "session_persistence": "ephemeral",
            "crash_recovery": "new_session_required_after_process_restart",
            "secret_injection": False,
            "storage_state_persistence": False,
            "downloads": False,
            "uploads": False,
            "url_policy": "execution-layer pre/post navigation plus backend redirect/popup enforcement",
        }

    def effective_allowed_domains(self, requested: Sequence[str]) -> tuple[str, ...]:
        requested_tuple = tuple(str(item) for item in requested if str(item or "").strip())
        configured = tuple(self.browser_allowed_domains)
        if not configured:
            return requested_tuple
        if not requested_tuple:
            return configured
        for rule in requested_tuple:
            if not _domain_rule_within(rule, configured):
                raise ValueError(f"requested browser domain rule is outside runtime policy: {rule}")
        return requested_tuple

    def set_permission_mode(self, session_id, mode):
        current = self.get_session(session_id)
        if self.browser_sessions is not None and str(current.permission_mode.value) != str(getattr(mode, "value", mode)):
            self.browser_sessions.close_owner(session_id)
        return super().set_permission_mode(session_id, mode)

    def recover_interrupted(self, session_id):
        if self.browser_sessions is not None:
            self.browser_sessions.close_owner(session_id)
        return super().recover_interrupted(session_id)

    def close(self) -> None:
        if self.browser_sessions is not None:
            self.browser_sessions.close_all()
        super().close()


def _domain_rule_within(rule: str, configured: tuple[str, ...]) -> bool:
    value = str(rule or "").strip().casefold().rstrip(".")
    for outer_raw in configured:
        outer = str(outer_raw or "").strip().casefold().rstrip(".")
        if value == outer:
            return True
        if outer.startswith("*."):
            suffix = outer[2:]
            inner = value[2:] if value.startswith("*.") else value
            if inner == suffix or inner.endswith("." + suffix):
                return True
    return False


__all__ = [
    "BrowserRuntime",
    "BrowserSessionHandle",
    "BrowserSessionStore",
    "BrowserStateSnapshot",
    "redact_browser_text",
    "redact_browser_url",
]
