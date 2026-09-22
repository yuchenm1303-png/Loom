from __future__ import annotations

import ipaddress
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, Sequence
from urllib.parse import urlsplit

from .storage import utc_now


# Allowing the browser to reach local addresses and allowing it to reach the
# endpoints that hand out credentials are different questions. These were one
# list, checked only when private networks were forbidden, so the moment that
# became configurable a cloud metadata service would have become reachable too.
LOCAL_HOST_NAMES = frozenset({"localhost", "localhost.localdomain"})
INFRASTRUCTURE_HOST_NAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "host.docker.internal",
        "gateway.docker.internal",
        "kubernetes.default",
        "kubernetes.default.svc",
    }
)
# The instance metadata services. Link-local otherwise stays governed by the
# private-network switch; these two addresses never are.
CLOUD_METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)


class BrowserError(RuntimeError):
    pass


class BrowserSessionLimitError(BrowserError):
    """A browser slot is temporarily unavailable.

    Keep capacity failures typed instead of making callers parse an English error
    string. Tool handlers can then recover/reuse an owned browser or report a
    retryable busy state without turning ordinary session contention into a fatal
    agent error.
    """

    def __init__(self, message: str, *, scope: str, limit: int) -> None:
        super().__init__(message)
        self.scope = str(scope)
        self.limit = max(1, int(limit))


class BrowserUnavailableError(BrowserError):
    pass


class BrowserURLPolicyError(BrowserError):
    pass


class BrowserTextNotFoundError(BrowserError):
    """A text search completed and matched nothing.

    Its own type because not finding text is an ordinary answer, and because the
    backend must translate the provider's error rather than let the tool layer
    match on message text: browser-use ships an unrelated class also named
    BrowserError, so `except BrowserError` never catches it.
    """


@dataclass(frozen=True, slots=True)
class BrowserLaunchOptions:
    headless: bool = True
    allowed_domains: tuple[str, ...] = ()
    # True when this session drives a browser Loom did not launch. Such a browser
    # legitimately holds chrome://, extension and localhost tabs that Loom must
    # hide rather than reject, and the decision belongs to the session because a
    # model can attach to an external browser while the runtime default is to
    # launch its own.
    external_browser: bool = False

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
    tabs: tuple[dict[str, object], ...] = ()
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

    def click_at(self, x: int, y: int, button: str = "left") -> BrowserPageState:
        ...

    def send_text(self, text: str) -> BrowserPageState:
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


class BrowserURLPolicy:
    """Backward-compatible constructor for Loom's canonical BrowserSecurityPolicy.

    BrowserSessionManager historically shipped a second URL-policy implementation
    here. Keeping two security rules in sync caused real drift, including metadata
    handling. The public name remains for embedders, but construction now delegates
    to the single implementation in browser_security.py.
    """

    def __new__(cls, *args, **kwargs):
        from .browser_security import BrowserSecurityPolicy

        return BrowserSecurityPolicy(*args, **kwargs)


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
        self._starting_total = 0
        self._starting_by_owner: dict[str, int] = {}

    def start(
        self,
        owner_session_id: str,
        *,
        headless: bool = True,
        allowed_domains: Sequence[str] = (),
        backend_factory: BrowserBackendFactory | None = None,
        external_browser: bool = False,
    ) -> ManagedBrowserSession:
        """Open one browser session for an owner.

        backend_factory overrides the store's default for this session only, so a
        caller can put sessions on different browsers concurrently without
        changing what the next session gets.
        """

        owner = _key(owner_session_id, "owner_session_id")
        options = BrowserLaunchOptions(
            headless=bool(headless),
            allowed_domains=tuple(allowed_domains),
            external_browser=bool(external_browser),
        )
        factory = backend_factory or self.backend_factory
        if not callable(factory):
            raise TypeError("browser backend_factory must be callable")

        with self._lock:
            if len(self._sessions) + self._starting_total >= self.max_sessions_total:
                raise BrowserSessionLimitError(
                    f"browser session limit reached ({self.max_sessions_total})",
                    scope="total",
                    limit=self.max_sessions_total,
                )
            owned = sum(1 for item in self._sessions.values() if item.owner_session_id == owner)
            owned += self._starting_by_owner.get(owner, 0)
            if owned >= self.max_sessions_per_owner:
                raise BrowserSessionLimitError(
                    f"browser session limit for Loom session reached ({self.max_sessions_per_owner})",
                    scope="owner",
                    limit=self.max_sessions_per_owner,
                )
            self._starting_total += 1
            self._starting_by_owner[owner] = self._starting_by_owner.get(owner, 0) + 1

        backend: BrowserBackend | None = None
        try:
            backend = factory(options)
            state = backend.start()
            state = self._validated_state(state, options, origin="start")
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
        except Exception:
            if backend is not None:
                try:
                    backend.close()
                except Exception:
                    pass
            with self._lock:
                self._release_start_reservation_locked(owner)
            raise

        with self._lock:
            self._release_start_reservation_locked(owner)
            self._sessions[managed.browser_id] = managed
        return managed

    def _release_start_reservation_locked(self, owner: str) -> None:
        self._starting_total = max(0, self._starting_total - 1)
        remaining = self._starting_by_owner.get(owner, 0) - 1
        if remaining > 0:
            self._starting_by_owner[owner] = remaining
        else:
            self._starting_by_owner.pop(owner, None)

    def list(self, owner_session_id: str) -> tuple[dict[str, object], ...]:
        owner = _key(owner_session_id, "owner_session_id")
        with self._lock:
            items = [item for item in self._sessions.values() if item.owner_session_id == owner]
        items.sort(key=lambda item: (item.created_at, item.browser_id))
        return tuple(item.snapshot() for item in items)

    def active_count(self) -> int:
        """Live or opening sessions across every owner.

        Reconfiguring the browser connection has to know whether any model still
        holds a browser_id or is currently opening one. Counting reservations
        prevents a mode switch from racing backend.start().
        """

        with self._lock:
            return len(self._sessions) + self._starting_total

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

    def _validated_state(
        self,
        state: BrowserPageState,
        options: BrowserLaunchOptions,
        *,
        origin: str = "update",
    ) -> BrowserPageState:
        if not isinstance(state, BrowserPageState):
            raise TypeError("browser backend must return BrowserPageState")
        if state.url and state.url != "about:blank":
            try:
                self.url_policy.validate(state.url, allowed_domains=options.allowed_domains)
            except BrowserURLPolicyError as exc:
                # Where the browser already was is not a navigation Loom made,
                # and refusing the attach over it meant the browser could not be
                # opened at all while the user happened to be sitting on a new
                # tab page. What came back said only that http/https was
                # required, so the model retried the URL it had asked for -
                # observed six times in thirty seconds, once with no URL at all,
                # which no reading of that message could explain.
                #
                # The page still must not be read. Dropping the DOM is what
                # withholds it; the session survives, and browser_navigate moves
                # somewhere Loom may actually work. A navigation Loom performs
                # later is a different matter and still fails closed below.
                if origin != "start":
                    raise
                return _unreadable_start_state(state, exc)
        return state


def _browser_internal(url: str) -> bool:
    """A page belonging to the browser itself - a new tab, settings, devtools.

    These carry no site content, so naming one back costs nothing and tells the
    model exactly why the page cannot be read. A blocked http(s) destination is
    the opposite case: the host is the thing policy refused, and a session
    scoped to one set of domains must not learn the address of a page outside
    them just because the user had it open.
    """

    scheme = urlsplit(str(url or "")).scheme.casefold()
    return bool(scheme) and scheme not in {"http", "https"}


def _unreadable_start_state(state: BrowserPageState, reason: Exception) -> BrowserPageState:
    """The attached page, with the page itself withheld.

    Loom is connected and every other tool works; only this one page is off
    limits. Saying so - and saying what to do instead - is the difference
    between one browser_navigate and a retry loop against an error that named
    no URL. The shape follows what the extension already returns for a
    privileged page it cannot inject into.
    """

    internal = _browser_internal(state.url)
    info = dict(state.page_info or {})
    info["readable"] = False
    info["recovery"] = {
        "action": "browser_navigate",
        "automatic": True,
        "creates_loom_work_tab": False,
        "reason": "attached_page_outside_policy",
    }
    detail = str(reason) if internal else "this page is outside the policy for this browser session"
    return BrowserPageState(
        url=state.url if internal else "",
        title=state.title if internal else "",
        dom="",
        tabs=state.tabs,
        page_info=info,
        errors=tuple(state.errors)
        + (
            f"Loom attached to the browser but cannot read the page it was on: {detail}. "
            "The browser session is open and working - continue automatically with browser_navigate "
            "to the destination you need. Do not ask the user to switch tabs.",
        ),
    )


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
