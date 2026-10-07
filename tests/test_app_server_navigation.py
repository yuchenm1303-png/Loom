import io
import json
import threading
from types import SimpleNamespace

from app.app_server import JsonRpcStdioServer


def test_navigation_reads_do_not_block_new_selection_or_control_and_drain_on_eof():
    slow_started = threading.Event()
    fast_finished = threading.Event()
    control_finished = threading.Event()
    release = threading.Event()
    server = JsonRpcStdioServer(SimpleNamespace(subscribe_notifications=lambda callback: None))

    def handle(payload):
        request_id = payload["id"]
        if request_id == 1:
            slow_started.set()
            assert release.wait(3)
        elif request_id == 2:
            assert slow_started.wait(2)
            fast_finished.set()
        else:
            control_finished.set()
        return {"jsonrpc": "2.0", "id": request_id, "result": {"loaded": request_id}}

    server.controller = SimpleNamespace(handle=handle, initialized=True)
    requests = [
        {"id": 1, "method": "thread/read", "params": {"threadId": "long", "presentationOnly": True}},
        {"id": 2, "method": "thread/read", "params": {"threadId": "new", "presentationOnly": True}},
        {"id": 3, "method": "runtime/status", "params": {}},
    ]
    reader = io.StringIO("".join(json.dumps(request) + "\n" for request in requests))
    writer = io.StringIO()
    serving = threading.Thread(target=lambda: server.serve(reader, writer), daemon=True)
    serving.start()
    try:
        assert fast_finished.wait(2), "new selection waited for the previous long read"
        assert control_finished.wait(2), "navigation blocked the control worker"
        assert serving.is_alive(), "EOF discarded a pending navigation response"
    finally:
        release.set()
        serving.join(3)
    assert not serving.is_alive()
    responses = [json.loads(line) for line in writer.getvalue().splitlines()]
    assert sorted(response["id"] for response in responses) == [1, 2, 3]
    assert next(index for index, response in enumerate(responses) if response["id"] == 2) < next(
        index for index, response in enumerate(responses) if response["id"] == 1)
