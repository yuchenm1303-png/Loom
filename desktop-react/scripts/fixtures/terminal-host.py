"""Isolated real-PTY host for the browser terminal integration test."""
from __future__ import annotations

import json
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from app.agent_runtime.contracts import PermissionMode
from app.agent_runtime.process_runtime import ProcessStore
from app.code_block_terminal import _dispatch

store = ProcessStore()
with tempfile.TemporaryDirectory(prefix='loom-terminal-browser-') as directory:
    root = Path(directory)
    (root / 'subfolder').mkdir()
    sessions = [SimpleNamespace(session_id='browser-terminal-test', workspace_dir=root,
                                permission_mode=PermissionMode.FULL_ACCESS)]
    service = SimpleNamespace(runtime=SimpleNamespace(process_store=store),
                              _list_session_objects=lambda: sessions, _is_active=lambda _: False)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                result = _dispatch(service, request['method'], {**request.get('params', {}), 'workspace': directory})
                payload = {'result': result}
            except Exception as error:
                payload = {'error': str(error)}
            data = json.dumps(payload, ensure_ascii=True).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    print(json.dumps({'port': server.server_port}), flush=True)
    try:
        server.serve_forever()
    finally:
        store.terminate_all()
