from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop.rpc import DesktopEventBridge, RpcRunner, connect_client


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    # Worker threads emit into queued connections, so delivery needs a running
    # loop. Use QApplication so the whole desktop suite shares one instance type.
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


def _wait(predicate, timeout=2.0):
    """Pump the event loop until a queued signal has actually been delivered."""
    app = QApplication.instance()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("desktop RPC condition did not become true")


def test_results_and_errors_reach_the_bridge():
    bridge = DesktopEventBridge()
    runner = RpcRunner(bridge)
    results, errors = [], []
    bridge.rpcResult.connect(lambda tag, payload: results.append((tag, payload)))
    bridge.rpcError.connect(lambda tag, message: errors.append((tag, message)))

    runner.submit("ok", lambda: {"value": 1})
    _wait(lambda: results)
    assert results == [("ok", {"value": 1})]

    runner.submit("boom", lambda: (_ for _ in ()).throw(RuntimeError("nope")))
    _wait(lambda: errors)
    assert errors == [("boom", "RuntimeError: nope")]


def test_a_newer_request_supersedes_the_pending_one_instead_of_being_dropped():
    bridge = DesktopEventBridge()
    runner = RpcRunner(bridge)
    delivered = []
    bridge.rpcResult.connect(lambda tag, payload: delivered.append(payload))

    release = threading.Event()

    def slow():
        release.wait(2.0)
        return "stale"

    runner.submit("snapshot", slow)
    # The second submission is what the user actually asked for; the old client
    # discarded it because a call with the same tag was already in flight.
    runner.submit("snapshot", lambda: "fresh")
    _wait(lambda: delivered)
    assert delivered == ["fresh"]

    # The superseded call finishing later must not overwrite the fresh answer.
    release.set()
    for _ in range(20):
        QApplication.instance().processEvents()
        time.sleep(0.01)
    assert delivered == ["fresh"]


def test_closing_stops_further_delivery():
    bridge = DesktopEventBridge()
    runner = RpcRunner(bridge)
    delivered = []
    bridge.rpcResult.connect(lambda tag, payload: delivered.append(payload))

    runner.close()
    runner.submit("threads", lambda: "late")
    time.sleep(0.05)

    assert delivered == []
    assert runner.pending_tags == frozenset()


def test_connect_client_only_subscribes_to_callbacks_that_exist():
    class PartialClient:
        def __init__(self):
            self.listener = None

        def subscribe_notifications(self, listener):
            self.listener = listener

    bridge = DesktopEventBridge()
    client = PartialClient()
    connect_client(client, bridge)

    seen = []
    bridge.notification.connect(lambda method, params: seen.append(method))
    client.listener("turn/started", {})
    assert seen == ["turn/started"]
