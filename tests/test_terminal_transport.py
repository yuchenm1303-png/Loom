import io
import json
import threading
from types import SimpleNamespace

from app.app_server import JsonRpcStdioServer


def test_terminal_keystrokes_bypass_busy_controls_but_keep_fifo_order():
    entered = threading.Event()
    release = threading.Event()
    input_finished = threading.Event()
    writes = []
    server = JsonRpcStdioServer(SimpleNamespace(subscribe_notifications=lambda _: None))
    def handle(payload):
        if payload['method'] == 'settings/set':
            entered.set()
            assert release.wait(3)
        else:
            assert entered.wait(2)
            writes.append(payload['params']['text'])
            if len(writes) == 3:
                input_finished.set()
        return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {}}
    server.controller = SimpleNamespace(initialized=True, handle=handle)
    requests = [{'id': 0, 'method': 'settings/set', 'params': {}}] + [
        {'id': i + 1, 'method': 'terminal/session/write', 'params': {'text': text}}
        for i, text in enumerate(['echo ', 'hello', '\r'])]
    writer = io.StringIO()
    thread = threading.Thread(target=lambda: server.serve(io.StringIO(''.join(json.dumps(r) + '\n' for r in requests)), writer), daemon=True)
    thread.start()
    try:
        assert input_finished.wait(2), 'terminal input waited behind an unrelated control operation'
        assert writes == ['echo ', 'hello', '\r']
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert sorted(json.loads(line)['id'] for line in writer.getvalue().splitlines()) == [0, 1, 2, 3]


def test_only_existing_terminal_io_uses_the_fast_lane():
    for method in ['terminal/session/read', 'terminal/session/write', 'terminal/session/resize']:
        assert JsonRpcStdioServer._is_terminal_io({'method': method})
    for method in ['terminal/session/create', 'terminal/session/close', 'turn/start', 'thread/delete', 'initialize']:
        assert not JsonRpcStdioServer._is_terminal_io({'method': method})
    assert not JsonRpcStdioServer._is_terminal_io(None)
    assert not JsonRpcStdioServer._is_terminal_io({'method': []})


def test_terminal_requests_queued_during_initialize_are_drained_at_eof():
    release = threading.Event()
    received = threading.Event()
    server = JsonRpcStdioServer(SimpleNamespace(subscribe_notifications=lambda _: None))
    controller = SimpleNamespace(initialized=False)
    def handle(payload):
        if payload['method'] == 'initialize':
            controller.initialized = True
        elif payload['method'] == 'settings/set':
            assert release.wait(3)
        else:
            received.set()
        return {'jsonrpc': '2.0', 'id': payload['id'], 'result': {}}
    controller.handle = handle
    server.controller = controller
    requests = [{'id': index, 'method': method, 'params': {}} for index, method in enumerate(
        ['initialize', 'settings/set', 'terminal/session/read'])]
    writer = io.StringIO()
    thread = threading.Thread(target=lambda: server.serve(io.StringIO(''.join(json.dumps(r) + '\n' for r in requests)), writer), daemon=True)
    thread.start()
    try:
        # Before initialize has completed, admission keeps protocol ordering.
        # Once accepted controls finish, EOF must still drain the I/O lane.
        release.set()
        assert received.wait(2), 'an accepted terminal read was dropped during initialization/EOF'
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()
    assert sorted(json.loads(line)['id'] for line in writer.getvalue().splitlines()) == [0, 1, 2]
