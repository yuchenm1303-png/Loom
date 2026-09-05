from __future__ import annotations

import ipaddress
import socket
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, Sequence
from urllib.parse import urlsplit

from .storage import utc_now


class BrowserError(RuntimeError):
    pass


class BrowserUnavailableError(BrowserError):
    pass


class BrowserURLPolicyError(BrowserError):
    pass


@dataclass(frozen=True, slots=True)
class BrowserLaunchOptions:
    headless: bool = True
    allowed_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalized: list[str] = []
        for raw in self.allowed_domains:
            value = str(raw or "").strip().casefold().rstrip(".")
            if not value:
                continue
            if "://" in value or "/" in value or "@" in value:
                raise ValueError("browser allowed_domains entries must be host patterns, not URLs")
            if value.startswith("*."):
                host = value[2:]
                if not host or "*" in host:
                    raise ValueError(f"invalid browser domain pattern: {raw!r}")
            elif "*" in value:
                raise ValueError(f"invalid browser domain pattern: {raw!r}")
            if value not in normalized:
                normalized.append(value)
        object.__setattr__(self, "allowed_domains", tuple(normalized))


@dataclass(frozen=True, slots=True)
class BrowserPageState:
    url: str
    title: str
    dom: str = ""
    tabs: tuple[dict[str, str], ...] = ()
    page_info: dict[str, object] | None = None
    errors: tuple[str, ...] = ()

    def to_dict(self, *, max_dom_chars: int = 30_000) -> dict[str, object]:
        limit = max(1, int(max_dom_chars))
        dom = self.dom
        truncated = len(dom) > limit
        if truncated:
            dom = dom[:limit] + "\n...[DOM truncated by Loom]"
        return {
            "url": self.url,
            "title": self.title,
            "tabs": list(self.tabs),
            "page_info": self.page_info,
            "errors": list(self.errors),
            "dom": dom,
            "dom_truncated": truncated,
        }


class BrowserBackend(Protocol):
    @property
    def backend_name(self) -> str:
        ...

    def start(self) -> BrowserPageState:
        ...

    def state(self) -> BrowserPageState:
        ...

    def navigate(self, url: str, *, new_tab: bool = False) -> BrowserPageState:
        ...

    def click(self, index: int) -> BrowserPageState:
        ...

    def type_text(self, index: int, text: str, *, clear: bool = True) -> BrowserPageState:
        ...

    def scroll(self, direction: str, amount: int) -> BrowserPageState:
        ...

    def go_back(self) -> BrowserPageState:
        ...

    def screenshot(self, *, full_page: bool = False) -> bytes:
        ...

    def close(self) -> None:
        ...


BrowserBackendFactory = Callable[[BrowserLaunchOptions], BrowserBackend]
DNSResolver = Callable[[str], Sequence[str]]


def _default_resolver(hostname: str) -> tuple[str, ...]:
    values: list[str] = []
    try:
        rows = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise BrowserURLPolicyError(f"browser destination DNS lookup failed: {hostname}") from exc
    for row in rows:
        address = str(row[4][0]).split("%", 1)[0]
        if address not in values:
            values.append(address)
    if not values:
        raise BrowserURLPolicyError(f"browser destination did not resolve: {hostname}")
    return tuple(values)


@dataclass(frozen=True, slots=True)
class BrowserURLPolicy:
    """Public-web URL policy applied before explicit navigation and after actions.

    This is an application boundary, not a complete network sandbox. The optional
    browser-use backend additionally enables its own navigation SecurityWatchdog
    with IP-address blocking and allowed-domain enforcement. Loom resolves explicit
    navigation hostnames and rejects any non-global address by default to reduce
    localhost/private-network SSRF exposure.
    """

    allow_private_networks: bool = False
    resolve_dns: bool = True
    resolver: DNSResolver = field(default=_default_resolver, repr=False, compare=False)

    _blocked_names = frozenset(
        {
            "localhost",
            "localhost.localdomain",
            "metadata.google.internal",
            "metadata",
            "host.docker.internal",
            "gateway.docker.internal",
            "kubernetes.default",
            "kubernetes.default.svc",
        }
    )

    def validate(self, url: str, *, allowed_domains: tuple[str, ...] = ()) -> str:
        value = str(url or "").strip()
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"}:
            raise BrowserURLPolicyError("browser navigation only allows http/https URLs")
        if parsed.username is not None or parsed.password is not None:
            raise BrowserURLPolicyError("browser navigation URLs must not contain userinfo credentials")
        host = (parsed.hostname or "").casefold().rstrip(".")
        if not host:
            raise BrowserURLPolicyError("browser navigation URL must contain a hostname")
        if host in self._blocked_names or host.endswith(".localhost"):
            if not self.allow_private_networks:
                raise BrowserURLPolicyError(f"browser navigation to local host is blocked: {host}")
        if allowed_domains and not _matches_domains(host, allowed_domains):
            raise BrowserURLPolicyError(f"browser destination is outside this session's allowed domains: {host}")

        literal = _parse_ip(host)
        if literal is not None:
            self._validate_ip(literal)
        elif self.resolve_dns and not self.allow_private_networks:
            for address in self.resolver(host):
                try:
                    resolved = ipaddress.ip_address(str(address).split("%", 1)[0])
                except ValueError as exc:
                    raise BrowserURLPolicyError("browser DNS resolver returned an invalid address") from exc
                self._validate_ip(resolved)
        return value

    def _validate_ip(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        if self.allow_private_networks:
            return
        if not address.is_global:
            raise BrowserURLPolicyError(f"browser navigation to non-public IP is blocked: {address}")


def _parse_ip(host: str):
    try:
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None


def _matches_domains(host: str, rules: tuple[str, ...]) -> bool:
    for rule in rules:
        value = str(rule).casefold().rstrip(".")
        if value.startswith("*."):
            suffix = value[2:]
            if host.endswith("." + suffix):
                return True
        elif host == value:
            return True
    return False


@dataclass(slots=True)
class ManagedBrowserSession:
    browser_id: str
    owner_session_id: str
    backend: BrowserBackend
    options: BrowserLaunchOptions
    created_at: str
    updated_at: str
    last_state: BrowserPageState

    def snapshot(self) -> dict[str, object]:
        return {
            "browser_id": self.browser_id,
            "owner_session_id": self.owner_session_id,
            "backend": self.backend.backend_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "headless": self.options.headless,
            "allowed_domains": list(self.options.allowed_domains),
            "url": self.last_state.url,
            "title": self.last_state.title,
        }


class BrowserSessionManager:
    """Owns ephemeral browser sessions and enforces Loom-session ownership."""

    def __init__(
        self,
        backend_factory: BrowserBackendFactory,
        *,
        url_policy: BrowserURLPolicy | None = None,
        max_sessions_per_owner: int = 2,
        max_sessions_total: int = 8,
    ) -> None:
        if not callable(backend_factory):
            raise TypeError("browser backend_factory must be callable")
        self.backend_factory = backend_factory
        self.url_policy = url_policy or BrowserURLPolicy()
        self.max_sessions_per_owner = max(1, int(max_sessions_per_owner))
        self.max_sessions_total = max(1, int(max_sessions_total))
        self._lock = threading.RLock()
        self._sessions: dict[str, ManagedBrowserSession] = {}

    def start(
        self,
        owner_session_id: str,
        *,
        headless: bool = True,
        allowed_domains: Sequence[str] = (),
    ) -> ManagedBrowserSession:
        owner = _key(owner_session_id, "owner_session_id")
        options = BrowserLaunchOptions(
            headless=bool(headless),
            allowed_domains=tuple(allowed_domains),
        )
        with self._lock:
            if len(self._sessions) >= self.max_sessions_total:
                raise BrowserError(f"browser session limit reached ({self.max_sessions_total})")
            owned = sum(1 for item in self._sessions.values() if item.owner_session_id == owner)
            if owned >= self.max_sessions_per_owner:
                raise BrowserError(
                    f"browser session limit for Loom session reached ({self.max_sessions_per_owner})"
                )
        backend = self.backend_factory(options)
        try:
            state = backend.start()
            state = self._validated_state(state, options)
        except Exception:
            try:
                backend.close()
            except Exception:
                pass
            raise
        now = utc_now()
        managed = ManagedBrowserSession(
            browser_id=str(uuid.uuid4()),
            owner_session_id=owner,
            backend=backend,
            options=options,
            created_at=now,
            updated_at=now,
            last_state=state,
        )
        with self._lock:
            self._sessions[managed.browser_id] = managed
        return managed

    def list(self, owner_session_id: str) -> tuple[dict[str, object], ...]:
        owner = _key(owner_session_id, "owner_session_id")
        with self._lock:
            items = [item for item in self._sessions.values() if item.owner_session_id == owner]
        items.sort(key=lambda item: (item.created_at, item.browser_id))
        return tuple(item.snapshot() for item in items)

    def state(self, owner_session_id: str, browser_id: str) -> BrowserPageState:
        item = self._owned(owner_session_id, browser_id)
        state = item.backend.state()
        return self._update_state(item, state)

    def navigate(
        self,
        owner_session_id: str,
        browser_id: str,
        url: str,
        *,
        new_tab: bool = False,
    ) -> BrowserPageState:
        item = self._owned(owner_session_id, browser_id)
        target = self.url_policy.validate(url, allowed_domains=item.options.allowed_domains)
        state = item.backend.navigate(target, new_tab=bool(new_tab))
        return self._update_state(item, state)

    def click(self, owner_session_id: str, browser_id: str, index: int) -> BrowserPageState:
        item = self._owned(owner_session_id, browser_id)
        state = item.backend.click(int(index))
        return self._update_state(item, state)

    def type_text(
        self,
        owner_session_id: str,
        browser_id: str,
        index: int,
        text: str,
        *,
        clear: bool = True,
    ) -> BrowserPageState:
        item = self._owned(owner_session_id, browser_id)
        value = str(text)
        if len(value) > 100_000:
            raise ValueError("browser typed text exceeds 100,000 characters")
        state = item.backend.type_text(int(index), value, clear=bool(clear))
        return self._update_state(item, state)

    def scroll(
        self,
        owner_session_id: str,
        browser_id: str,
        direction: str,
        amount: int,
    ) -> BrowserPageState:
        item = self._owned(owner_session_id, browser_id)
        resolved_direction = str(direction or "").casefold()
        if resolved_direction not in {"up", "down", "left", "right"}:
            raise ValueError("browser scroll direction must be up/down/left/right")
        pixels = int(amount)
        if not 1 <= pixels <= 20_000:
            raise ValueError("browser scroll amount must be within 1..20000 pixels")
        state = item.backend.scroll(resolved_direction, pixels)
        return self._update_state(item, state)

    def go_back(self, owner_session_id: str, browser_id: str) -> BrowserPageState:
        item = self._owned(owner_session_id, browser_id)
        state = item.backend.go_back()
        return self._update_state(item, state)

    def screenshot(self, owner_session_id: str, browser_id: str, *, full_page: bool = False) -> bytes:
        item = self._owned(owner_session_id, browser_id)
        data = item.backend.screenshot(full_page=bool(full_page))
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise BrowserError("browser backend returned an empty screenshot")
        if len(data) > 25_000_000:
            raise BrowserError("browser screenshot exceeds 25 MB")
        return bytes(data)

    def close(self, owner_session_id: str, browser_id: str) -> bool:
        item = self._owned(owner_session_id, browser_id)
        try:
            item.backend.close()
        finally:
            with self._lock:
                self._sessions.pop(item.browser_id, None)
        return True

    def close_owner(self, owner_session_id: str) -> int:
        owner = _key(owner_session_id, "owner_session_id")
        with self._lock:
            ids = [item.browser_id for item in self._sessions.values() if item.owner_session_id == owner]
        closed = 0
        for browser_id in ids:
            try:
                self.close(owner, browser_id)
                closed += 1
            except Exception:
                with self._lock:
                    self._sessions.pop(browser_id, None)
        return closed

    def close_all(self) -> int:
        with self._lock:
            items = list(self._sessions.values())
        closed = 0
        for item in items:
            try:
                item.backend.close()
                closed += 1
            except Exception:
                pass
            finally:
                with self._lock:
                    self._sessions.pop(item.browser_id, None)
        return closed

    def _owned(self, owner_session_id: str, browser_id: str) -> ManagedBrowserSession:
        owner = _key(owner_session_id, "owner_session_id")
        key = _key(browser_id, "browser_id")
        with self._lock:
            item = self._sessions.get(key)
        if item is None:
            raise KeyError(f"browser session not found: {key}")
        if item.owner_session_id != owner:
            raise PermissionError("browser session belongs to a different Loom session")
        return item

    def _update_state(self, item: ManagedBrowserSession, state: BrowserPageState) -> BrowserPageState:
        try:
            checked = self._validated_state(state, item.options)
        except Exception:
            # A click/type/back action can navigate. If the backend-level navigation
            # guard missed a prohibited destination, tear down the browser immediately
            # rather than continuing from an untrusted local/private target.
            try:
                item.backend.close()
            finally:
                with self._lock:
                    self._sessions.pop(item.browser_id, None)
            raise
        item.last_state = checked
        item.updated_at = utc_now()
        return checked

    def _validated_state(self, state: BrowserPageState, options: BrowserLaunchOptions) -> BrowserPageState:
        if not isinstance(state, BrowserPageState):
            raise TypeError("browser backend must return BrowserPageState")
        if state.url and state.url != "about:blank":
            self.url_policy.validate(state.url, allowed_domains=options.allowed_domains)
        return state


def _key(value: str, name: str) -> str:
    key = str(value or "").strip()
    if not key:
        raise ValueError(f"{name} must not be empty")
    return key


__all__ = [
    "BrowserBackend",
    "BrowserBackendFactory",
    "BrowserError",
    "BrowserLaunchOptions",
    "BrowserPageState",
    "BrowserSessionManager",
    "BrowserURLPolicy",
    "BrowserURLPolicyError",
    "BrowserUnavailableError",
    "ManagedBrowserSession",
]
