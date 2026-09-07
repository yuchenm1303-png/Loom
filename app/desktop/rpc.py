"""Threading boundary between the App Server client and Qt's UI thread.

The old client de-duplicated background calls by tag and dropped any request
that arrived while one with the same tag was in flight, so quickly switching
threads could leave the window showing an older conversation. Here a newer
request for a tag always wins: it supersedes the pending one instead of being
discarded, and the superseded reply is ignored when it lands.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal


class DesktopEventBridge(QObject):
    """Marshal App Server callbacks and background RPC results onto the UI thread."""

    notification = Signal(str, object)
    stderr = Signal(str)
    serverExited = Signal(str)
    rpcResult = Signal(str, object)
    rpcError = Signal(str, str)


class RpcRunner:
    """Run App Server calls off the UI thread, newest-wins per tag."""

    def __init__(self, bridge: DesktopEventBridge) -> None:
        self._bridge = bridge
        self._guard = threading.Lock()
        self._sequence: dict[str, int] = {}
        self._closed = False

    @property
    def pending_tags(self) -> frozenset[str]:
        with self._guard:
            return frozenset(self._sequence)

    def is_pending(self, tag: str) -> bool:
        with self._guard:
            return tag in self._sequence

    def close(self) -> None:
        with self._guard:
            self._closed = True
            self._sequence.clear()

    def submit(self, tag: str, operation: Callable[[], Any]) -> None:
        tag = str(tag)
        with self._guard:
            if self._closed:
                return
            ticket = self._sequence.get(tag, 0) + 1
            self._sequence[tag] = ticket

        def runner() -> None:
            try:
                result = operation()
            except Exception as exc:  # surfaced to the UI, never raised into Qt
                self._deliver(tag, ticket, error=f"{type(exc).__name__}: {exc}")
            else:
                self._deliver(tag, ticket, result=result)

        threading.Thread(
            target=runner,
            name=f"loom-desktop-rpc-{tag[:24]}",
            daemon=True,
        ).start()

    def _deliver(self, tag: str, ticket: int, *, result: Any = None, error: str = "") -> None:
        with self._guard:
            if self._closed or self._sequence.get(tag) != ticket:
                # A newer request for this tag has already been issued; its
                # answer is the one the UI should act on.
                return
            self._sequence.pop(tag, None)
        if error:
            self._bridge.rpcError.emit(tag, error)
        else:
            self._bridge.rpcResult.emit(tag, result)


def connect_client(client: Any, bridge: DesktopEventBridge) -> None:
    """Subscribe the bridge to whichever callbacks the client actually exposes."""
    subscribe = getattr(client, "subscribe_notifications", None)
    if callable(subscribe):
        subscribe(lambda method, params: bridge.notification.emit(method, params))
    subscribe = getattr(client, "subscribe_stderr", None)
    if callable(subscribe):
        subscribe(bridge.stderr.emit)
    subscribe = getattr(client, "subscribe_exit", None)
    if callable(subscribe):
        subscribe(bridge.serverExited.emit)


__all__ = ["DesktopEventBridge", "RpcRunner", "connect_client"]
