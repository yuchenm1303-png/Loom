from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.ai import ModelResponse, ToolCall

from .browser_backend import BrowserUseSessionBackend
from .browser_extension_bridge import BrowserExtensionBridge, BrowserExtensionSessionBackend
from .browser_security import BrowserSecurityPolicy
from .browser_session import (
    BrowserBackendFactory,
    BrowserLaunchOptions,
    BrowserPageState,
    BrowserSessionManager,
    BrowserURLPolicyError,
    ManagedBrowserSession,
)
from .browser_use_backend import browser_use_available
from .memory_store import redact_secrets
from .tools import ToolRegistry
from .web_search_runtime import WebSearchRuntime


_BROWSER_CONNECTION_MODES = frozenset({"local-launch", "cdp-attach", "extension"})
# browser-use resolves a channel name to the installed browser itself. Loom only
# maps its own user-facing engine choice onto that vocabulary; "system" keeps
# browser-use's default so an unusual install is not forced onto a missing build.
_BROWSER_ENGINE_CHANNELS = {"edge": "msedge", "chrome": "chrome"}


def _browser_channel_for_engine(engine: str) -> str:
    return _BROWSER_ENGINE_CHANNELS.get(str(engine or "").strip().casefold(), "")


# Chrome/Edge default to 9222, and the usual way to run more than one debuggable
# browser is to step the port. A model cannot guess a port, so Loom reports the
# ones that answer instead of letting it invent endpoints. Deliberately a short
# fixed list: this must not become a local port scanner.
_CDP_DISCOVERY_PORTS = (9222, 9223, 9224, 9225)
_CDP_DISCOVERY_TIMEOUT = 0.35


def _probe_local_cdp_endpoint(port: int, timeout: float = _CDP_DISCOVERY_TIMEOUT) -> dict[str, Any] | None:
    """Identify a DevTools endpoint on a loopback port, or return None."""

    import json as _json
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{int(port)}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if getattr(response, "status", 200) != 200:
                return None
            payload = _json.loads(response.read(64_000).decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, _json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    browser = str(payload.get("Browser") or "").strip()
    if not browser:
        return None
    return {
        "cdp_url": f"http://127.0.0.1:{int(port)}",
        "browser": browser[:120],
        # The websocket URL is a control channel, not something the model needs.
        "protocol_version": str(payload.get("Protocol-Version") or "")[:16],
    }


def discover_local_cdp_endpoints(
    ports: Sequence[int] = _CDP_DISCOVERY_PORTS,
) -> tuple[dict[str, Any], ...]:
    found: list[dict[str, Any]] = []
    for port in ports:
        endpoint = _probe_local_cdp_endpoint(port)
        if endpoint is not None:
            found.append(endpoint)
    return tuple(found)


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
    """Remove secret-shaped browser arguments before Runtime can persist them."""

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

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

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


def _default_browser_profile_dir(store_root: str | Path) -> Path:
    """Resolve Loom's browser profile outside durable per-session state."""

    sessions_root = Path(store_root).expanduser().resolve()
    if sessions_root.name == "sessions" and sessions_root.parent.name == "agent_runtime":
        runtime_root = sessions_root.parent.parent
    else:
        runtime_root = sessions_root.parent
    return (runtime_root / "browser" / "profiles" / "default").resolve()


def _prepare_profile_dir(path: str | Path) -> Path:
    profile_dir = Path(path).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    try:
        profile_dir.chmod(0o700)
    except OSError:
        # Windows ACLs and some network filesystems do not map cleanly to POSIX
        # modes. The profile still stays under the current user's Loom home.
        pass
    return profile_dir


def _validate_local_cdp_url(value: str) -> str:
    """Accept only explicit loopback Chrome DevTools endpoints.

    CDP grants full control of every page in the attached browser. It is therefore
    runtime configuration, never a model tool argument, and remote hosts are rejected
    even when the caller would otherwise allow private-network browser navigation.
    """

    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("browser CDP URL is invalid") from exc
    if parsed.scheme.casefold() not in {"http", "https", "ws", "wss"}:
        raise ValueError("browser CDP URL must use http/https/ws/wss")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("browser CDP URL must not contain credentials")
    host = str(parsed.hostname or "").strip()
    if not host:
        raise ValueError("browser CDP URL must contain a loopback IP hostname")
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError as exc:
        raise ValueError("browser CDP URL must use literal 127.0.0.1 or ::1, not a hostname") from exc
    if not address.is_loopback:
        raise ValueError("browser CDP URL is restricted to the local loopback interface")
    if port is None or not 1 <= int(port) <= 65535:
        raise ValueError("browser CDP URL must include an explicit port")
    if parsed.query or parsed.fragment:
        raise ValueError("browser CDP URL must not contain query parameters or fragments")
    return raw


def _env_flag_enabled(name: str) -> bool:
    value = str(os.environ.get(name) or "").strip()
    if not value:
        return False
    return value.casefold() not in {"0", "false", "off", "no", "disabled"}


def _extension_backend_requested() -> bool:
    backend = str(os.environ.get("LOOM_BROWSER_BACKEND") or "").strip().casefold()
    return backend in {"extension", "browser-extension", "current-tab"} or _env_flag_enabled("LOOM_BROWSER_EXTENSION")


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
    """Loom-session-owned live browser handles.

    Live handles, tabs, selector maps and element revisions are never serialized into
    Loom session state. Local browser-use launches may reuse Loom's persistent profile;
    CDP mode instead attaches to a user-owned local Chrome/Edge process and disconnects
    without terminating that process.
    """

    def __init__(self, *args, filter_unsafe_background_tabs: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.filter_unsafe_background_tabs = bool(filter_unsafe_background_tabs)

    def _validated_state(self, state: BrowserPageState, options: BrowserLaunchOptions) -> BrowserPageState:
        # The active tab is always fail-closed. CDP attachment creates/switches to a
        # neutral about:blank work tab before the first state capture, so any later
        # active private/internal destination indicates an actual navigation escape.
        checked = super()._validated_state(state, options)
        safe_tabs: list[dict[str, str]] = []
        for tab in checked.tabs:
            url = str(tab.get("url", "")) if isinstance(tab, dict) else ""
            if not url or url == "about:blank":
                safe_tabs.append(tab)
                continue
            try:
                self.url_policy.validate(url, allowed_domains=options.allowed_domains)
            except BrowserURLPolicyError:
                if self.filter_unsafe_background_tabs or options.external_browser:
                    # Existing Chrome/Edge can contain localhost, chrome:// and
                    # extension tabs that Loom must neither expose nor control. They
                    # stay open in the user's browser but disappear from model state.
                    continue
                raise
            safe_tabs.append(tab)
        if len(safe_tabs) == len(checked.tabs):
            return checked
        return BrowserPageState(
            url=checked.url,
            title=checked.title,
            dom=checked.dom,
            tabs=tuple(safe_tabs),
            page_info=checked.page_info,
            errors=checked.errors,
        )

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
        browser_persist_profile: bool = True,
        browser_profile_dir: str | Path | None = None,
        browser_cdp_url: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        # The base Runtime persists ModelResponse tool arguments immediately. Put
        # browser-specific secret scrubbing in front of that durable boundary.
        self.platform = _BrowserSecretBoundaryPlatform(self.platform)

        self.browser_headless = bool(browser_headless)
        self.browser_allowed_domains = tuple(str(item) for item in browser_allowed_domains)
        self.browser_security_policy = browser_security_policy or BrowserSecurityPolicy()
        # Remember how this runtime was constructed so a later connection change
        # can rebuild the backend from the same starting point instead of
        # inheriting whatever the previous mode happened to leave behind.
        self._browser_custom_factory = browser_backend_factory
        self._browser_auto_configure = bool(auto_configure_browser)
        self._browser_engine = ""
        self._browser_connection_configured = False
        # When on, browser_open accepts a connection instead of always using the
        # configured default. Attaching to a browser the user is signed into means
        # any page it holds can steer the model, so this stays a visible switch
        # rather than an implicit capability.
        self.browser_model_controlled_connection = False
        # The user's standing choice, kept apart from browser_profile_persistence,
        # which is a per-mode derived fact: cdp-attach and extension drive a browser
        # that owns its own profile, so they always report no Loom profile. Reading
        # the preference back out of that derived flag made a round trip through
        # either mode silently downgrade local-launch to an ephemeral profile.
        self._browser_persist_preference = bool(browser_persist_profile)

        explicit_cdp = browser_cdp_url is not None
        configured_cdp = browser_cdp_url
        if configured_cdp is None and browser_backend_factory is None and auto_configure_browser:
            configured_cdp = os.environ.get("LOOM_BROWSER_CDP_URL")
        if explicit_cdp and not str(configured_cdp or "").strip():
            raise ValueError("browser_cdp_url must not be empty")

        self._configure_browser_connection(
            cdp_url=str(configured_cdp or ""),
            extension=browser_backend_factory is None
            and auto_configure_browser
            and _extension_backend_requested(),
            persist_profile=bool(browser_persist_profile),
            profile_dir=browser_profile_dir,
        )

    def _configure_browser_connection(
        self,
        *,
        cdp_url: str,
        extension: bool,
        persist_profile: bool,
        profile_dir: str | Path | None,
        engine: str = "",
    ) -> None:
        """Build the browser backend, session store and mode-specific tools.

        Loom supports three ways to reach a browser - launching its own, attaching
        to a local CDP endpoint, and driving the user's current tab through the
        extension bridge - and they differ in profile ownership, tab filtering and
        what browser_open should tell the model. All of that is derived here so the
        desktop can switch modes without rebuilding the whole runtime stack.
        """

        factory = self._browser_custom_factory
        auto_configure_browser = self._browser_auto_configure
        browser_profile_dir = profile_dir
        browser_persist_profile = bool(persist_profile)
        backend_name = "custom" if factory is not None else "disabled"
        cdp_url = _validate_local_cdp_url(str(cdp_url or ""))
        extension_requested = bool(extension) and factory is None and auto_configure_browser
        if extension_requested and cdp_url:
            raise ValueError("LOOM_BROWSER_BACKEND=extension cannot be combined with browser_cdp_url/LOOM_BROWSER_CDP_URL")
        if cdp_url and factory is not None:
            raise ValueError("browser_cdp_url cannot be combined with a custom browser backend factory")
        if cdp_url and browser_profile_dir is not None:
            raise ValueError("browser_cdp_url cannot be combined with browser_profile_dir")
        if cdp_url and not auto_configure_browser:
            raise ValueError("browser_cdp_url requires auto_configure_browser=True")

        profile_dir: Path | None = None
        profile_persistence = False
        cdp_attached = False
        extension_attached = False
        extension_bridge: BrowserExtensionBridge | None = None
        if extension_requested:
            if browser_profile_dir is not None:
                raise ValueError("LOOM_BROWSER_BACKEND=extension cannot be combined with browser_profile_dir")
            extension_bridge = BrowserExtensionBridge.from_environment()
            extension_bridge.start()

            def build_extension_backend(options: BrowserLaunchOptions):
                assert extension_bridge is not None
                return BrowserExtensionSessionBackend(options=options, bridge=extension_bridge)

            factory = build_extension_backend
            backend_name = "browser-extension"
            extension_attached = True
        elif factory is None and auto_configure_browser and browser_use_available():
            if cdp_url:
                cdp_attached = True
            elif browser_persist_profile:
                configured = browser_profile_dir or _default_browser_profile_dir(self.store.root)
                profile_dir = _prepare_profile_dir(configured)
                profile_persistence = True

            channel = _browser_channel_for_engine(engine)
            private = bool(
                getattr(self.browser_security_policy, "allow_private_networks", False)
            )

            def build_browser_use_backend(options: BrowserLaunchOptions):
                backend = BrowserUseSessionBackend(options=options)
                backend.user_data_dir = profile_dir
                backend.cdp_url = cdp_url or None
                backend.browser_channel = channel
                # browser-use enforces its own private-address rule, so Loom's
                # policy has to be mirrored onto the backend or the two disagree.
                backend.allow_private_networks = private
                return backend

            factory = build_browser_use_backend
            backend_name = "browser-use"
        elif cdp_url:
            raise RuntimeError("browser_cdp_url requires the browser-use extra")

        self.browser_backend_name = backend_name
        self._browser_cdp_url = cdp_url
        self._browser_engine = str(engine or "")
        self.browser_profile_persistence = profile_persistence
        self.browser_profile_dir = profile_dir
        self.browser_cdp_attached = cdp_attached
        self.browser_extension_attached = extension_attached
        self.browser_extension_bridge = extension_bridge
        # Never expose or persist the configured control endpoint in tool/status
        # payloads. Keep it only inside the backend closure used for attachment.
        exclusive_browser = profile_persistence or cdp_attached or extension_attached
        self.browser_sessions = (
            BrowserSessionStore(
                factory,
                url_policy=self.browser_security_policy,
                max_sessions_per_owner=1 if exclusive_browser else 2,
                max_sessions_total=1 if exclusive_browser else 8,
                filter_unsafe_background_tabs=cdp_attached or extension_attached,
            )
            if factory is not None
            else None
        )

        from .browser_tools import browser_tools

        for raw_tool in browser_tools(self):
            tool = raw_tool
            if extension_attached and raw_tool.name == "browser_open":
                tool = replace(
                    raw_tool,
                    description=(
                        "Open Browser Use through the installed Loom Browser Extension. Pass the task's target http/https "
                        "URL whenever it is known. Loom reuses an exact page or its own work tab, otherwise it creates a "
                        "purple Loom tab group and leaves the user's unrelated tab untouched. If the current page is "
                        "edge://, chrome://, or otherwise protected, call browser_navigate instead of asking the user to "
                        "switch pages. Return a bounded LLM-facing DOM state. The extension bridge token is never exposed. "
                        "Existing out-of-policy background tabs remain open but are hidden from Loom. allowed_domains "
                        "can restrict the session."
                    ),
                )
            elif cdp_attached and raw_tool.name == "browser_open":
                tool = replace(
                    raw_tool,
                    description=(
                        "Open Loom's configured existing local Chrome/Edge browser session through its loopback-only CDP "
                        "connection, optionally navigate to an http/https URL, and return a bounded LLM-facing DOM state. "
                        "The model cannot choose or inspect the CDP endpoint. Closing Loom disconnects without terminating "
                        "the user's browser. Existing out-of-policy background tabs remain open but are hidden from Loom. "
                        "allowed_domains can restrict the session."
                    ),
                )
            elif profile_persistence and raw_tool.name == "browser_open":
                tool = replace(
                    raw_tool,
                    description=(
                        "Open Loom's local browser-use session using the persistent default browser profile, optionally "
                        "navigate to an http/https URL, and return a bounded LLM-facing DOM state. Cookies and ordinary "
                        "site storage can survive browser restarts; live tabs and element indexes do not. allowed_domains "
                        "can restrict the session. No browser-profile filesystem path or cookie value is exposed to the model."
                    ),
                )
            existing = self.tools.get(tool.name)
            if existing is None:
                self.tools.register(tool)
            elif self._browser_connection_configured and existing.description != tool.description:
                # browser_open's description is the only place the model learns
                # which browser it is about to drive. A mode switch has to rewrite
                # it, or the model keeps being told it is opening an ephemeral
                # browser while it is really steering the user's own tabs. The
                # registry has no in-place update, so rebuild it around the change
                # and keep every other tool's exposure exactly as it was.
                self.tools = ToolRegistry(
                    tuple(
                        tool if candidate.name == tool.name else candidate
                        for candidate in self.tools.all()
                    )
                )
        self._browser_connection_configured = True

    @property
    def browser_allow_private_networks(self) -> bool:
        return bool(getattr(self.browser_security_policy, "allow_private_networks", False))

    def browser_set_private_networks(self, allowed: bool) -> dict[str, object]:
        """Allow or forbid browser navigation to loopback and private addresses.

        Off by default because a browser that can reach the local network is a
        way to read services that were never exposed. It has to be reachable
        though: without it Loom's browser cannot open the user's own dev server,
        which is most of what a coding agent would point a browser at.
        """

        wanted = bool(allowed)
        if wanted == self.browser_allow_private_networks:
            return {"allow_private_networks": wanted, "changed": False}
        store = self.browser_sessions
        if store is not None and store.active_count():
            raise RuntimeError(
                "close the active browser session before changing private-network access"
            )
        self.browser_security_policy = replace(
            self.browser_security_policy, allow_private_networks=wanted
        )
        if store is not None:
            # The store holds the policy it was built with, and the backend
            # enforces its own copy of the rule, so both have to follow.
            store.url_policy = self.browser_security_policy
        self._configure_browser_connection(
            cdp_url=self._browser_cdp_url,
            extension=self.browser_extension_attached,
            persist_profile=self._browser_persist_preference,
            profile_dir=None,
            engine=self._browser_engine,
        )
        return {"allow_private_networks": wanted, "changed": True}

    def browser_attachable_browsers(self) -> tuple[dict[str, Any], ...]:
        """Local browsers that browser_open connect=attach can drive.

        Empty unless the user allowed the model to choose the browser, so the
        switch also controls whether Loom looks for endpoints at all.
        """

        if not self.browser_model_controlled_connection:
            return ()
        if not browser_use_available():
            return ()
        return discover_local_cdp_endpoints()

    def _extension_bridge_for_session(self) -> BrowserExtensionBridge:
        """Reuse the runtime's bridge, starting one if this is not extension mode.

        A model asking for the current tab while Loom's default is to launch its
        own browser still needs a bridge, and starting a second one would bind a
        port the installed extension is not polling.
        """

        bridge = self.browser_extension_bridge
        if bridge is None:
            bridge = BrowserExtensionBridge.from_environment()
            bridge.start()
            self.browser_extension_bridge = bridge
        return bridge

    def browser_session_connection(
        self,
        connect: str,
        *,
        cdp_url: str = "",
    ) -> tuple[BrowserBackendFactory, bool, str]:
        """Build a backend for one requested connection, without changing the default.

        Returns the factory, whether the target is a browser Loom does not own,
        and a label for status/diagnostics. Per-session so a model can attach to
        an external browser without altering what the next session connects to.
        """

        requested = str(connect or "").strip().casefold().replace("-", "_")
        if requested in {"", "default"}:
            store = self.browser_sessions
            if store is None:
                raise RuntimeError("browser backend is unavailable")
            external = bool(self.browser_cdp_attached or self.browser_extension_attached)
            return store.backend_factory, external, self.browser_connection_mode

        if requested == "current_tab":
            bridge = self._extension_bridge_for_session()

            def build_extension(options: BrowserLaunchOptions):
                return BrowserExtensionSessionBackend(options=options, bridge=bridge)

            return build_extension, True, "extension"

        if requested not in {"launch", "attach"}:
            raise ValueError("browser connect must be one of: launch, attach, current_tab")
        if not browser_use_available():
            raise RuntimeError("this browser connection requires the browser-use extra")

        channel = _browser_channel_for_engine(self._browser_engine)
        # Mirrored onto every per-session backend for the same reason as the
        # configured default: browser-use enforces its own private-address rule.
        private = bool(getattr(self.browser_security_policy, "allow_private_networks", False))
        if requested == "attach":
            # Loopback only, exactly as for the configured default. A model may
            # pick which local browser to drive; it may not point Loom at a host.
            endpoint = _validate_local_cdp_url(str(cdp_url or ""))
            if not endpoint:
                raise ValueError("attaching to a browser requires a loopback CDP URL with an explicit port")

            def build_attached(options: BrowserLaunchOptions):
                backend = BrowserUseSessionBackend(options=options)
                backend.user_data_dir = None
                backend.cdp_url = endpoint
                backend.browser_channel = channel
                backend.allow_private_networks = private
                return backend

            return build_attached, True, "cdp-attach"

        profile_dir = self.browser_profile_dir if self._browser_persist_preference else None

        def build_launched(options: BrowserLaunchOptions):
            backend = BrowserUseSessionBackend(options=options)
            backend.user_data_dir = profile_dir
            backend.cdp_url = None
            backend.browser_channel = channel
            backend.allow_private_networks = private
            return backend

        return build_launched, False, "local-launch"

    @property
    def browser_connection_mode(self) -> str:
        if self.browser_extension_attached:
            return "extension"
        if self.browser_cdp_attached:
            return "cdp-attach"
        if self.browser_sessions is None:
            return "disabled"
        return "local-launch"

    def browser_set_connection(
        self,
        mode: str,
        *,
        cdp_url: str = "",
        persist_profile: bool | None = None,
        engine: str = "",
    ) -> dict[str, object]:
        """Switch which browser Loom drives, closing whatever it drove before.

        Changing modes mid-task would leave the model holding browser_ids and
        element indexes that belong to a browser it no longer controls, so the
        caller has to settle active sessions first.
        """

        requested = str(mode or "").strip().casefold()
        if requested not in _BROWSER_CONNECTION_MODES:
            raise ValueError(
                "browser connection mode must be one of: "
                + ", ".join(sorted(_BROWSER_CONNECTION_MODES))
            )
        if self._browser_custom_factory is not None:
            raise RuntimeError("a custom browser backend factory owns this runtime's browser connection")
        if not self._browser_auto_configure:
            raise RuntimeError("browser auto-configuration is disabled for this runtime")

        store = self.browser_sessions
        if store is not None and store.active_count():
            raise RuntimeError("close the active browser session before changing the browser connection")

        endpoint = str(cdp_url or "").strip()
        if requested == "cdp-attach" and not endpoint:
            raise ValueError("cdp-attach requires a loopback CDP URL")
        if requested != "cdp-attach":
            endpoint = ""

        previous_bridge = self.browser_extension_bridge
        if persist_profile is not None:
            self._browser_persist_preference = bool(persist_profile)
        persist = self._browser_persist_preference
        self._configure_browser_connection(
            cdp_url=endpoint,
            extension=requested == "extension",
            persist_profile=persist,
            profile_dir=None,
            engine=str(engine or self._browser_engine),
        )
        if previous_bridge is not None and previous_bridge is not self.browser_extension_bridge:
            try:
                previous_bridge.stop()
            except Exception:
                # A bridge that cannot be stopped must not strand the runtime in
                # the old mode; the new backend is already installed.
                pass
        return self.browser_status()

    def browser_status(self, owner_session_id: str | None = None) -> dict[str, object]:
        store = self.browser_sessions
        active = 0
        if store is not None and owner_session_id:
            active = len(store.list(owner_session_id))
        persistent = bool(self.browser_profile_persistence)
        attached = bool(self.browser_cdp_attached)
        extension = bool(self.browser_extension_attached)
        if extension:
            persistence = "extension-current-browser"
            recovery = "extension_reconnects_to_local_bridge"
            profile_name = "current-browser-extension"
            connection = "extension-bridge"
        elif attached:
            persistence = "external-browser"
            recovery = "reattach_to_configured_local_browser"
            profile_name = "external"
            connection = "cdp-attach"
        elif persistent:
            persistence = "profile-persistent"
            recovery = "new_session_reuses_persistent_profile"
            profile_name = "default"
            connection = "local-launch"
        else:
            persistence = "ephemeral"
            recovery = "new_session_required_after_process_restart"
            profile_name = ""
            connection = "local-launch" if store is not None else "disabled"
        status: dict[str, object] = {
            "enabled": store is not None,
            "backend": self.browser_backend_name,
            "browser_connection": connection,
            "external_browser": attached or extension,
            "cdp_endpoint_exposed": False,
            "active_sessions": active,
            "session_persistence": persistence,
            "crash_recovery": recovery,
            "secret_injection": False,
            "storage_state_persistence": persistent or attached or extension,
            "profile_name": profile_name,
            "profile_path_exposed": False,
            "model_controlled_connection": bool(self.browser_model_controlled_connection),
            "downloads": False,
            "uploads": False,
            "url_policy": "execution-layer pre/post navigation plus backend redirect/popup enforcement",
        }
        if self.browser_extension_bridge is not None:
            bridge_status = self.browser_extension_bridge.status()
            status["extension_bridge"] = {
                "connected": bool(bridge_status.get("connected")),
                "last_client_id": str(bridge_status.get("last_client_id") or ""),
                "last_client_version": str(bridge_status.get("last_client_version") or ""),
                "browser": str(bridge_status.get("browser") or ""),
                "current_tab": bridge_status.get("current_tab"),
                "pending_commands": int(bridge_status.get("pending_commands") or 0),
                "queued_commands": int(bridge_status.get("queued_commands") or 0),
                "url_exposed": False,
                "token_exposed": False,
            }
        return status

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
        if self.browser_extension_bridge is not None:
            self.browser_extension_bridge.stop()
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
