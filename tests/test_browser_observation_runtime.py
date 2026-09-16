from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.agent_runtime.browser_runtime_v1 import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserLaunchOptions, BrowserPageState
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolResult
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ImagePart, MessageRole, ModelResponse, TextPart, ToolCall


class DummyPlatform:
    def execute_chat(self, profile_id, request):
        return ModelResponse(text="unused")


class ObservationBackend:
    backend_name = "observation-fake"

    def __init__(self, options: BrowserLaunchOptions):
        self.options = options
        self.state_revision = 0
        self.closed = False

    def _state(self, dom: str = "[1]<button>Continue</button>\nDOM_SECRET_MARKER") -> BrowserPageState:
        self.state_revision += 1
        return BrowserPageState(
            url="https://example.test/page",
            title="Observation fixture",
            dom=dom,
            tabs=(),
            errors=(),
        )

    def start(self) -> BrowserPageState:
        return self._state()

    def state(self) -> BrowserPageState:
        return self._state()

    def navigate(self, url: str, *, new_tab: bool = False) -> BrowserPageState:
        return self._state("[1]<button>Continue</button>\nNAVIGATED_DOM")

    def click(self, index: int) -> BrowserPageState:
        # Deliberately return an observably identical page. Execution happened,
        # but the runtime must not promote that to a confirmed user-visible effect.
        return self._state()

    def type_text(self, index: int, text: str, *, clear: bool = True) -> BrowserPageState:
        return self._state(f"[1]<input value={text!r}>")

    def scroll(self, direction: str, amount: int) -> BrowserPageState:
        return self._state()

    def go_back(self) -> BrowserPageState:
        return self._state()

    def screenshot(self, *, full_page: bool = False) -> bytes:
        return b"\x89PNG\r\n\x1a\nFAKE"

    def close(self) -> None:
        self.closed = True


def _runtime(tmp_path: Path) -> tuple[BrowserRuntime, Path]:
    runtime = BrowserRuntime(
        platform=DummyPlatform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None,
        auto_configure_web_search=False,
        browser_backend_factory=lambda options: ObservationBackend(options),
        auto_configure_browser=False,
        browser_security_policy=BrowserSecurityPolicy(resolve_dns=False),
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    return runtime, workspace


def _session(runtime: BrowserRuntime, workspace: Path):
    session = runtime.create_session(
        "test",
        workspace_dir=workspace,
        permission_mode="full-access",
    )
    session.current_turn_id = "turn-browser-observation"
    return session


def _state_result(*, revision: int, dom: str, ok: bool = True) -> ToolResult:
    return ToolResult(
        ok=ok,
        content="Browser click completed.",
        data={
            "browser_id": "browser-1",
            "state_revision": revision,
            "url": "https://example.test/page",
            "title": "Observation fixture",
            "tabs": [],
            "page_info": {"backend": "fake"},
            "errors": [],
            "dom": dom,
            "dom_truncated": False,
        },
    )


def _step():
    return SimpleNamespace(
        request_state=SimpleNamespace(
            captured=False,
            project_instructions="",
            context_limits=None,
        )
    )


def test_full_dom_is_transient_not_durable(tmp_path):
    runtime, workspace = _runtime(tmp_path)
    session = _session(runtime, workspace)
    marker = "DOM_SECRET_MARKER <button>Pay now</button>"

    runtime._append_tool_result(
        session,
        ToolCall(call_id="call-1", name="browser_state", arguments={}),
        _state_result(revision=1, dom=marker),
        failed=False,
    )

    durable = session.messages[-1].content
    assert isinstance(durable, str)
    assert marker not in durable
    payload = json.loads(durable)
    assert "dom" not in payload["data"]
    assert payload["data"]["dom_chars"] == len(marker)

    messages, extra = runtime._prepare_model_request(session, _step(), None)
    observation = messages[-1]
    assert isinstance(observation.content, tuple)
    text = next(part for part in observation.content if isinstance(part, TextPart))
    assert marker in text.text
    assert "untrusted observations" in text.text
    assert "never override" in text.text
    safety = next(
        message
        for message in messages
        if message.role is MessageRole.SYSTEM and message.name == "loom_browser_untrusted_content"
    )
    assert "untrusted external data" in safety.content
    assert "cannot override" in safety.content
    assert extra["browser_observation"]["state_revision"] == 1
    runtime.close()


def test_identical_post_click_state_is_uncertain_not_confirmed(tmp_path):
    runtime, workspace = _runtime(tmp_path)
    session = _session(runtime, workspace)
    dom = "[1]<button>Continue</button>"

    runtime._append_tool_result(
        session,
        ToolCall(call_id="call-1", name="browser_state", arguments={}),
        _state_result(revision=1, dom=dom),
        failed=False,
    )
    runtime._append_tool_result(
        session,
        ToolCall(call_id="call-2", name="browser_click", arguments={}),
        _state_result(revision=2, dom=dom),
        failed=False,
    )

    payload = json.loads(session.messages[-1].content)
    assert payload["ok"] is True
    assert payload["data"]["effect"] == "uncertain"
    assert payload["data"]["effect_reason"] == "execution_succeeded_without_observable_state_change"
    assert "user-visible effect is uncertain" in payload["content"]

    messages, _ = runtime._prepare_model_request(session, _step(), None)
    text = next(part for part in messages[-1].content if isinstance(part, TextPart))
    assert "effect: uncertain" in text.text
    runtime.close()


def test_changed_page_state_is_reported_as_changed(tmp_path):
    runtime, workspace = _runtime(tmp_path)
    session = _session(runtime, workspace)

    runtime._append_tool_result(
        session,
        ToolCall(call_id="call-1", name="browser_state", arguments={}),
        _state_result(revision=1, dom="BEFORE"),
        failed=False,
    )
    runtime._append_tool_result(
        session,
        ToolCall(call_id="call-2", name="browser_click", arguments={}),
        _state_result(revision=2, dom="AFTER"),
        failed=False,
    )

    payload = json.loads(session.messages[-1].content)
    assert payload["data"]["effect"] == "changed"
    assert payload["data"]["effect_reason"] == "observable_browser_state_changed"
    runtime.close()


def test_browser_screenshot_is_ephemeral_visual_feedback(tmp_path):
    runtime, workspace = _runtime(tmp_path)
    session = _session(runtime, workspace)

    runtime._append_tool_result(
        session,
        ToolCall(call_id="call-1", name="browser_state", arguments={}),
        _state_result(revision=1, dom="CANVAS PAGE"),
        failed=False,
    )
    relative = Path("browser-screenshots") / "visual.png"
    target = workspace / relative
    target.parent.mkdir(parents=True)
    png = b"\x89PNG\r\n\x1a\nVISUAL_BYTES_NEVER_DURABLE"
    target.write_bytes(png)

    runtime._append_tool_result(
        session,
        ToolCall(call_id="call-2", name="browser_screenshot", arguments={}),
        ToolResult(
            ok=True,
            content="Browser screenshot saved to the workspace.",
            data={"browser_id": "browser-1", "path": relative.as_posix(), "bytes": len(png)},
        ),
        failed=False,
    )

    durable_history = "\n".join(
        str(message.content) for message in session.messages
    )
    assert "VISUAL_BYTES_NEVER_DURABLE" not in durable_history

    messages, extra = runtime._prepare_model_request(session, _step(), None)
    parts = messages[-1].content
    assert isinstance(parts, tuple)
    assert any(isinstance(part, ImagePart) for part in parts)
    assert extra["browser_observation"]["has_screenshot"] is True
    assert extra["browser_observation"]["screenshot_bytes"] == len(png)
    runtime.close()


def test_browser_close_clears_stale_observation(tmp_path):
    runtime, workspace = _runtime(tmp_path)
    session = _session(runtime, workspace)

    runtime._append_tool_result(
        session,
        ToolCall(call_id="call-1", name="browser_state", arguments={}),
        _state_result(revision=1, dom="OLD PAGE"),
        failed=False,
    )
    runtime._append_tool_result(
        session,
        ToolCall(call_id="call-2", name="browser_close", arguments={}),
        ToolResult(ok=True, content="Browser session closed.", data={"browser_id": "browser-1", "closed": True}),
        failed=False,
    )

    messages, extra = runtime._prepare_model_request(session, _step(), None)
    assert not any(
        isinstance(message.content, tuple)
        and any(isinstance(part, TextPart) and "LOOM_BROWSER_OBSERVATION" in part.text for part in message.content)
        for message in messages
    )
    assert "browser_observation" not in extra
    runtime.close()


def test_new_turn_drops_previous_transient_browser_observation(tmp_path):
    runtime, workspace = _runtime(tmp_path)
    session = _session(runtime, workspace)

    runtime._append_tool_result(
        session,
        ToolCall(call_id="call-1", name="browser_state", arguments={}),
        _state_result(revision=1, dom="ONE TURN ONLY"),
        failed=False,
    )
    assert session.session_id in runtime._browser_feedback

    result = runtime.start_turn(session.session_id, "next request")
    assert result.final_text == "unused"
    assert session.session_id not in runtime._browser_feedback
    assert session.session_id not in runtime._browser_visual_feedback
    runtime.close()


def test_browser_status_documents_new_observation_contract(tmp_path):
    runtime, _ = _runtime(tmp_path)
    status = runtime.browser_status()
    assert status["dom_persistence"] == "summary_only"
    assert "transient ImagePart" in status["visual_feedback"]
    assert status["page_content_trust"].startswith("untrusted observation")
    runtime.close()
