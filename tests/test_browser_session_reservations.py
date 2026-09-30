from __future__ import annotations

import threading

import pytest

from app.agent_runtime.browser_session import (
    BrowserError,
    BrowserLaunchOptions,
    BrowserPageState,
    BrowserSessionManager,
)


class _BlockingBackend:
    backend_name = "blocking"

    def __init__(self, entered: threading.Event, release: threading.Event) -> None:
        self.entered = entered
        self.release = release
        self.closed = False

    def start(self) -> BrowserPageState:
        self.entered.set()
        if not self.release.wait(5):
            raise RuntimeError("test backend was not released")
        return BrowserPageState(url="about:blank", title="")

    def close(self) -> None:
        self.closed = True


class _FailingBackend:
    backend_name = "failing"

    def __init__(self) -> None:
        self.closed = False

    def start(self) -> BrowserPageState:
        raise RuntimeError("synthetic browser start failure")

    def close(self) -> None:
        self.closed = True


class _ImmediateBackend:
    backend_name = "immediate"

    def start(self) -> BrowserPageState:
        return BrowserPageState(url="about:blank", title="")

    def close(self) -> None:
        return None


def test_total_session_limit_counts_backend_that_is_still_starting() -> None:
    entered = threading.Event()
    release = threading.Event()
    created: list[_BlockingBackend] = []

    def factory(_options: BrowserLaunchOptions) -> _BlockingBackend:
        backend = _BlockingBackend(entered, release)
        created.append(backend)
        return backend

    manager = BrowserSessionManager(
        factory,
        max_sessions_total=1,
        max_sessions_per_owner=2,
    )
    result: dict[str, object] = {}

    def open_first() -> None:
        result["session"] = manager.start("owner-a")

    thread = threading.Thread(target=open_first)
    thread.start()
    assert entered.wait(2), "first backend never entered start()"

    # The first browser is not in _sessions yet, but it already owns the only
    # available slot. A second concurrent open must fail before constructing a
    # second backend.
    assert manager.active_count() == 1
    with pytest.raises(BrowserError, match="browser session limit reached"):
        manager.start("owner-b")
    assert len(created) == 1

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "session" in result
    assert manager.active_count() == 1

    manager.close_all()
    assert manager.active_count() == 0


@pytest.mark.parametrize("close_all", [False, True])
def test_retiring_owner_does_not_leak_a_connection_still_opening(close_all):
    entered, release = threading.Event(), threading.Event()
    backend = _BlockingBackend(entered, release)
    manager = BrowserSessionManager(lambda options: backend, max_sessions_total=1)
    errors = []

    def open_connection():
        try:
            manager.start("owner-a")
        except BrowserError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=open_connection)
    thread.start()
    assert entered.wait(2)
    if close_all:
        manager.close_all()
    else:
        manager.close_owner("owner-a")
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == ["browser owner ended while the connection was opening"]
    assert backend.closed
    assert manager.active_count() == 0


def test_per_owner_limit_counts_backend_that_is_still_starting() -> None:
    entered = threading.Event()
    release = threading.Event()

    def factory(_options: BrowserLaunchOptions) -> _BlockingBackend:
        return _BlockingBackend(entered, release)

    manager = BrowserSessionManager(
        factory,
        max_sessions_total=2,
        max_sessions_per_owner=1,
    )

    thread = threading.Thread(target=lambda: manager.start("owner-a"))
    thread.start()
    assert entered.wait(2), "first backend never entered start()"

    with pytest.raises(BrowserError, match="browser session limit for Loom session reached"):
        manager.start("owner-a")

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    manager.close_all()


def test_failed_backend_start_releases_reserved_slot() -> None:
    failing = _FailingBackend()
    calls = 0

    def factory(_options: BrowserLaunchOptions):
        nonlocal calls
        calls += 1
        if calls == 1:
            return failing
        return _ImmediateBackend()

    manager = BrowserSessionManager(
        factory,
        max_sessions_total=1,
        max_sessions_per_owner=1,
    )

    with pytest.raises(RuntimeError, match="synthetic browser start failure"):
        manager.start("owner-a")

    assert failing.closed is True
    assert manager.active_count() == 0

    session = manager.start("owner-a")
    assert session.backend.backend_name == "immediate"
    assert manager.active_count() == 1
    manager.close("owner-a", session.browser_id)
    assert manager.active_count() == 0
