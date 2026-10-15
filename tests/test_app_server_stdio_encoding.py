from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap


def test_app_server_protocol_forces_utf8_over_non_utf8_python_stdio():
    """Regression for Chinese Windows code pages corrupting Desktop JSON-RPC."""

    script = textwrap.dedent(
        r'''
        import json
        import sys

        from loom_app_server import _configure_protocol_stdio

        _configure_protocol_stdio()
        payload = json.loads(sys.stdin.readline())
        sys.stdout.write(json.dumps({"echo": payload["text"]}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        '''
    )
    env = os.environ.copy()
    # Reproduce a redirected Windows-style non-UTF-8 Python stdio configuration
    # even when this regression test is running on a UTF-8 CI host.
    env["PYTHONIOENCODING"] = "gbk"
    env["PYTHONUTF8"] = "0"

    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    request_text = "你好，Loom。中文流式回复应该保持完整。"
    stdin_bytes = (
        json.dumps({"text": request_text}, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    stdout, stderr = process.communicate(stdin_bytes, timeout=10.0)

    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
    response = json.loads(stdout.decode("utf-8"))
    assert response == {"echo": request_text}
    assert b"\xef\xbf\xbd" not in stdout  # UTF-8 replacement character


def test_jsonrpc_frame_is_lossless_with_legacy_windows_writer():
    """A Host may receive an inherited cp1252 stream despite Python UTF-8 flags.

    JSON escaping is semantically transparent for clients and protects the wire
    even when the supplied TextIO was constructed before UTF-8 reconfiguration.
    """
    import io
    import threading
    from app.app_server import JsonRpcStdioServer

    output = io.BytesIO()
    writer = io.TextIOWrapper(output, encoding="cp1252", errors="strict", write_through=True)
    server = object.__new__(JsonRpcStdioServer)
    server._writer = writer
    server._write_lock = threading.Lock()
    message = "状态：执行中 🧪" * 6000
    server._write_direct({"jsonrpc": "2.0", "id": 1, "result": {"text": message}})
    wire = output.getvalue()
    assert all(character < 128 for character in wire)
    assert json.loads(wire)["result"]["text"] == message
