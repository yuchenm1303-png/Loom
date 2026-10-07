import io
import json
import threading
from types import SimpleNamespace

from app.app_server import JsonRpcStdioServer


def test_eof_drains_accepted_requests_before_stopping_response_writer(monkeypatch):
    release = threading.Event()
    joining_worker = threading.Event()
    finished = threading.Event()
    original_join = threading.Thread.join

    def simulate_expired_join(thread, timeout=None):
        if thread.name == "loom-app-rpc":
            joining_worker.set()
            # Deterministically simulate a request taking longer than a finite
            # shutdown timeout, without slowing the regression by five seconds.
            return original_join(thread, timeout=0 if timeout is not None else None)
        return original_join(thread, timeout=timeout)

    monkeypatch.setattr(threading.Thread, "join", simulate_expired_join)
    server = JsonRpcStdioServer(SimpleNamespace(subscribe_notifications=lambda callback: None))

    def handle(payload):
        assert release.wait(2)
        return {"jsonrpc": "2.0", "id": payload["id"], "result": {"accepted": True}}

    server.controller = SimpleNamespace(handle=handle, initialized=True)
    reader = io.StringIO('\n'.join(json.dumps({"id": index}) for index in (1, 2)) + '\n')
    writer = io.StringIO()

    def serve():
        try:
            server.serve(reader, writer)
        finally:
            finished.set()

    serving = threading.Thread(target=serve, daemon=True)
    serving.start()
    try:
        assert joining_worker.wait(2)
        assert not finished.wait(0.1), "EOF discarded accepted requests before their responses"
    finally:
        release.set()
        original_join(serving, timeout=2)
    assert finished.is_set()
    assert [json.loads(line)["id"] for line in writer.getvalue().splitlines()] == [1, 2]
