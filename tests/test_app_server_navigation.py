import io
import json
import threading
from types import SimpleNamespace

from app.app_server import JsonRpcStdioServer


def test_navigation_bypasses_a_blocked_control_handler():
    blocked, release, read = threading.Event(), threading.Event(), threading.Event()
    server = JsonRpcStdioServer(SimpleNamespace(subscribe_notifications=lambda callback: None))
    def handle(payload):
        if payload["method"] == "project/git_repository":
            blocked.set()
            assert release.wait(3)
        else:
            read.set()
        return {"jsonrpc": "2.0", "id": payload["id"], "result": {}}
    server.controller = SimpleNamespace(handle=handle, initialized=True)
    class Reader:
        def __iter__(self):
            yield json.dumps({"id": 1, "method": "project/git_repository"}) + "\n"
            assert blocked.wait(2)
            yield json.dumps({"id": 2, "method": "thread/read", "params": {"presentationOnly": True}}) + "\n"
    serving = threading.Thread(target=lambda: server.serve(Reader(), io.StringIO()), daemon=True)
    serving.start()
    try:
        assert read.wait(2), "navigation was queued behind blocked Git control operation"
    finally:
        release.set()
        serving.join(3)
    assert not serving.is_alive()


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


def test_usage_insights_run_beside_the_control_worker_not_in_front_of_it():
    """The home screen asks for profile/insights at launch and it reads every
    conversation. Queued on the control worker it held `thread/list` back for
    as long as it ran, which is what the sidebar showed as an empty list."""
    insights_started = threading.Event()
    control_finished = threading.Event()
    release = threading.Event()
    server = JsonRpcStdioServer(SimpleNamespace(subscribe_notifications=lambda callback: None))

    def handle(payload):
        if payload["method"] == "profile/insights":
            insights_started.set()
            assert release.wait(3)
        else:
            assert insights_started.wait(2)
            control_finished.set()
        return {"jsonrpc": "2.0", "id": payload["id"], "result": {}}

    server.controller = SimpleNamespace(handle=handle, initialized=True)
    requests = [
        {"id": 1, "method": "profile/insights", "params": {"days": 371}},
        {"id": 2, "method": "thread/list", "params": {"view": "active"}},
    ]
    reader = io.StringIO("".join(json.dumps(request) + "\n" for request in requests))
    writer = io.StringIO()
    serving = threading.Thread(target=lambda: server.serve(reader, writer), daemon=True)
    serving.start()
    try:
        assert control_finished.wait(2), "thread/list waited for profile/insights"
    finally:
        release.set()
        serving.join(3)
    assert sorted(json.loads(line)["id"] for line in writer.getvalue().splitlines()) == [1, 2]


def test_only_read_only_requests_are_taken_off_the_control_worker():
    observational = JsonRpcStdioServer._is_observational
    assert observational({"method": "profile/insights", "params": {}})
    assert observational({"method": "thread/read", "params": {"presentationOnly": True}})
    assert observational({"method": "thread/read", "params": {"threadOnly": True}})
    # A full read, and anything that changes state, keeps its ordering.
    assert not observational({"method": "thread/read", "params": {}})
    for method in ("thread/list", "project/list", "turn/start", "thread/start", "settings/set"):
        assert not observational({"method": method, "params": {}})
    assert not observational("not a request")
