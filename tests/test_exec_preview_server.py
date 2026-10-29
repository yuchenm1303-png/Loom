from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.agent_runtime import AgentRuntime, AgentStatus, FileAgentSessionStore, PermissionMode
from app.agent_runtime.development_verification import feature_recipes
from app.agent_runtime.execution_action import _validate_exec_argument_shape
from app.agent_runtime.process_runtime import ProcessStore
from app.agent_runtime.process_tools import _url_answers, exec_tool
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import AGENT_FAST_ROLE, MessageRole, ModelResponse, ToolCall


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _server_argv(port: int) -> list[str]:
    # Waits before listening so the tool really has to poll for readiness.
    script = ("import time, http.server; time.sleep(0.6); "
              f"http.server.ThreadingHTTPServer(('127.0.0.1', {port}), http.server.SimpleHTTPRequestHandler).serve_forever()")
    return [sys.executable, "-u", "-c", script]


@pytest.fixture
def harness(tmp_path):
    store = ProcessStore()
    context = ToolContext("s", "t", tmp_path, permission_mode=PermissionMode.FULL_ACCESS.value,
                          services={"process_store": store})
    yield context, store, exec_tool()
    store.terminate_all()


def test_server_start_waits_for_the_url_and_a_repeat_reuses_it(harness):
    context, store, tool = harness
    port = _free_port()
    arguments = {"argv": _server_argv(port), "ready_url": f"http://127.0.0.1:{port}/missing"}

    first = tool.handler(context, dict(arguments))
    assert first.ok and first.data["ready"] is True and first.data["reused"] is False
    assert first.data["running"] is True
    assert _url_answers(f"http://127.0.0.1:{port}/")  # a 404 page still counts as up

    again = tool.handler(context, dict(arguments))
    assert again.ok and again.data["reused"] is True
    assert again.data["process_id"] == first.data["process_id"]
    assert len([s for s in store.list_for_session("s") if s.running]) == 1


def test_server_lives_past_the_default_command_timeout(harness):
    context, store, tool = harness
    port = _free_port()
    result = tool.handler(context, {"argv": _server_argv(port), "ready_url": f"http://127.0.0.1:{port}/"})
    managed = store.get(result.data["process_id"], session_id="s")
    assert managed.timeout_seconds == 3600


def test_server_that_exits_reports_failure_with_its_output(harness):
    context, _store, tool = harness
    port = _free_port()
    result = tool.handler(context, {"argv": [sys.executable, "-c", "import sys; print('port taken'); sys.exit(3)"],
                                    "ready_url": f"http://127.0.0.1:{port}/"})
    assert not result.ok and result.data["ready"] is False
    assert "exited before" in result.content and "port taken" in result.content


def test_server_not_ready_in_time_is_reported_and_left_running(harness):
    context, _store, tool = harness
    port = _free_port()
    result = tool.handler(context, {"argv": [sys.executable, "-c", "import time; time.sleep(30)"],
                                    "ready_url": f"http://127.0.0.1:{port}/", "ready_timeout_seconds": 1})
    assert not result.ok and result.data["ready"] is False and result.data["running"] is True
    assert "did not answer" in result.content


def test_url_already_served_elsewhere_is_not_started_twice(harness):
    context, store, tool = harness

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        result = tool.handler(context, {"argv": _server_argv(port), "ready_url": f"http://127.0.0.1:{port}/"})
        assert result.ok and result.data["reused"] is True and result.data["process_id"] is None
        assert "did not start it" in result.content
        assert store.list_for_session("s") == ()
    finally:
        server.shutdown()
        server.server_close()


def test_readiness_probe_ignores_a_configured_proxy(harness, monkeypatch):
    context, _store, tool = harness
    dead = _free_port()
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{dead}")
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{dead}")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    port = _free_port()
    result = tool.handler(context, {"argv": _server_argv(port), "ready_url": f"http://127.0.0.1:{port}/",
                                    "ready_timeout_seconds": 10})
    assert result.ok and result.data["ready"] is True


@pytest.mark.parametrize("arguments,message", [
    ({"ready_url": "http://example.com/"}, "localhost"),
    ({"ready_url": "file:///etc/passwd"}, "localhost"),
    ({"ready_url": "http://127.0.0.1:1/", "wait": True}, "background"),
    ({"ready_url": "http://127.0.0.1:1/", "ready_timeout_seconds": 0}, "ready_timeout_seconds"),
])
def test_server_start_rejects_unsafe_or_contradictory_arguments(harness, arguments, message):
    context, store, tool = harness
    with pytest.raises(ValueError, match=message):
        tool.handler(context, {"argv": [sys.executable, "-c", "pass"], **arguments})
    assert store.list_for_session("s") == ()


def test_exec_action_shape_accepts_the_server_arguments():
    _validate_exec_argument_shape({"argv": ["node"], "wait": False, "ready_url": "http://127.0.0.1:5199/",
                                   "ready_timeout_seconds": 30})
    with pytest.raises(ValueError, match="ready_url"):
        _validate_exec_argument_shape({"argv": ["node"], "ready_url": 5199})


def test_feature_recipe_carries_exact_server_start_arguments(tmp_path):
    context = ToolContext("s", "t", tmp_path)
    config = tmp_path / "loom.verification.json"
    server = {"argv": ["node", "vite.js", "--port", "5199"], "cwd": "web", "ready_url": "http://127.0.0.1:5199/"}
    feature = {"id": "panel", "server": "web", "sources": ["src/*.tsx"], "expectation": "visible"}
    config.write_text(json.dumps({"version": 1, "servers": {"web": server}, "features": [feature]}))
    recipes, status = feature_recipes(context, ["src/Panel.tsx"])
    assert status == "configured" and recipes[0]["start"] == server

    config.write_text(json.dumps({"version": 1, "servers": {}, "features": [feature]}))
    assert feature_recipes(context, ["src/Panel.tsx"])[1].startswith("invalid")


def test_repository_recipes_name_a_defined_server():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    context = ToolContext("s", "t", root)
    recipes, status = feature_recipes(context, ["desktop-react/src/components/Composer.tsx"])
    assert status == "configured"
    assert recipes[0]["start"]["ready_url"] == "http://127.0.0.1:5199/"


class PreviewPlatform:
    def __init__(self, port: int) -> None:
        self.port = port
        self.result = None

    def execute_chat(self, _profile_id, request):
        tools = [m for m in request.messages if m.role is MessageRole.TOOL]
        if not tools:
            return ModelResponse(tool_calls=(ToolCall(call_id="preview", name="exec", arguments={
                "argv": _server_argv(self.port), "ready_url": f"http://127.0.0.1:{self.port}/"}),))
        self.result = json.loads(tools[-1].content)
        return ModelResponse(text="preview up")


def test_agent_starts_a_preview_server_through_the_runtime(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    platform = PreviewPlatform(_free_port())
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "state"),
                           tools=loom_default_tools())
    session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=project,
                                     permission_mode=PermissionMode.FULL_ACCESS)
    try:
        result = runtime.start_turn(session.session_id, "Start the preview.")
        assert result.status is AgentStatus.COMPLETED
        assert platform.result["data"]["ready"] is True
        assert _url_answers(f"http://127.0.0.1:{platform.port}/")
    finally:
        runtime.process_store.terminate_all()
        runtime.close()
