from __future__ import annotations

import json
from pathlib import Path

from app.agent_runtime.browser_runtime_v1 import BrowserRuntime
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_session import BrowserLaunchOptions, BrowserPageState
from app.agent_runtime.contracts import AgentEventKind
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


def _step(runtime: BrowserRuntime, session):
    # Keep the observation tests on the same immutable Step contract the real
    # runtime captures. A hand-written request_state-only namespace went stale
    # when Context Runtime began reading world_state.sandbox.
    return runtime._build_step_context(session, next_model_step=True)


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

    messages, extra = runtime._prepare_model_request(session, _step(runtime, session), None)
    observation = messages[-1]
    assert isinstance(observation.content, tuple)
    assert observation.role is MessageRole.TOOL
    assert observation.tool_call_id == "call-1"
    text = next(part for part in observation.content if isinstance(part, TextPart) and "LOOM_BROWSER_OBSERVATION" in part.text)
    assert marker in text.text
    assert not any(message.role is MessageRole.USER for message in messages)
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

    messages, _ = runtime._prepare_model_request(session, _step(runtime, session), None)
    text = next(part for part in messages[-1].content if isinstance(part, TextPart) and "LOOM_BROWSER_OBSERVATION" in part.text)
    assert "effect: uncertain" in text.text
    runtime.close()


def test_browser_observation_is_not_replayed_after_committed_model_response(tmp_path):
    runtime, workspace = _runtime(tmp_path)
    session = _session(runtime, workspace)
    runtime._append_tool_result(
        session,
        ToolCall(call_id="call-1", name="browser_state", arguments={}),
        _state_result(revision=1, dom="FRESH DOM"),
        failed=False,
    )

    _, first_extra = runtime._prepare_model_request(session, _step(runtime, session), None)
    assert first_extra["browser_observation"]["state_revision"] == 1

    # A rejected sample may be retried with the same transient observation.
    runtime._record(session, AgentEventKind.MODEL_RESPONSE_REJECTED, data={"reason": "invalid_provider_response"})
    _, retry_extra = runtime._prepare_model_request(session, _step(runtime, session), None)
    assert retry_extra["browser_observation"]["state_revision"] == 1

    runtime._record(session, AgentEventKind.MODEL_RESPONSE, data={"text": "I saw the page."})
    messages, later_extra = runtime._prepare_model_request(session, _step(runtime, session), None)
    assert "browser_observation" not in later_extra
    assert not any(
        isinstance(message.content, tuple)
        and any(isinstance(part, TextPart) and "FRESH DOM" in part.text for part in message.content)
        for message in messages
    )

    # A subsequent browser result still provides fresh transient input.
    runtime._append_tool_result(
        session,
        ToolCall(call_id="call-2", name="browser_state", arguments={}),
        _state_result(revision=2, dom="NEW DOM"),
        failed=False,
    )
    _, fresh_extra = runtime._prepare_model_request(session, _step(runtime, session), None)
    assert fresh_extra["browser_observation"]["state_revision"] == 2
    runtime.close()


def test_effect_evidence_survives_a_model_step_without_replaying_dom(tmp_path):
    runtime, workspace = _runtime(tmp_path)
    session = _session(runtime, workspace)
    try:
        for index, name in enumerate(("browser_state", "browser_click_at", "browser_type")):
            runtime._append_tool_result(session, ToolCall(str(index), name, {}),
                _state_result(revision=index + 1, dom="SAME DOM" if index < 2 else "CHANGED DOM"), failed=False)
            payload = json.loads(session.messages[-1].content)
            assert payload["data"]["effect"] == ("observed", "uncertain", "changed")[index]
            runtime._record(session, AgentEventKind.MODEL_RESPONSE, data={"text": "Choosing next action"})
            assert session.session_id not in runtime._browser_feedback
        # A digest is comparison evidence only; it cannot inject stale page text.
        assert "DOM" not in str(runtime._browser_baselines)
    finally:
        runtime.close()


def test_observation_stays_with_its_source_when_a_later_browser_call_fails(tmp_path):
    runtime, workspace = _runtime(tmp_path)
    session = _session(runtime, workspace)
    try:
        runtime._append_tool_result(session, ToolCall("source", "browser_state", {}),
                                   _state_result(revision=1, dom="SOURCE_PAGE"), failed=False)
        runtime._append_tool_result(session, ToolCall("wrong-handle", "browser_click", {}),
                                   ToolResult(False, "unknown browser session"), failed=True)
        messages, extra = runtime._prepare_model_request(session, _step(runtime, session), None)
        observed = [m for m in messages if m.role is MessageRole.TOOL and isinstance(m.content, tuple)]
        assert [m.tool_call_id for m in observed] == ["source"]
        failed = next(m for m in messages if m.tool_call_id == "wrong-handle")
        assert isinstance(failed.content, str) and "SOURCE_PAGE" not in failed.content
        assert extra["browser_observation"]["source_call_id"] == "source"
    finally:
        runtime.close()


def test_comparison_baselines_are_per_browser(tmp_path):
    runtime, workspace = _runtime(tmp_path)
    session = _session(runtime, workspace)
    try:
        for browser_id in ("browser-1", "browser-2", "browser-1"):
            result = _state_result(revision=1, dom=browser_id)
            result = ToolResult(True, result.content, {**result.data, "browser_id": browser_id})
            runtime._append_tool_result(session, ToolCall(browser_id, "browser_click", {}), result, failed=False)
            runtime._record(session, AgentEventKind.MODEL_RESPONSE, data={"text": "Next"})
        assert json.loads(session.messages[-1].content)["data"]["effect"] == "uncertain"
    finally:
        runtime.close()


def test_dependent_action_batch_replans_from_the_actual_result_without_extra_refresh(tmp_path):
    from app.agent_runtime.contracts import AgentStatus
    from test_turn_stop import Scripted
    runtime, workspace = _runtime(tmp_path)
    session = runtime.create_session("test", workspace_dir=workspace, permission_mode="full-access")
    store = runtime.browser_sessions
    browser = store.start(session.session_id)
    backend = browser.backend
    dispatched = []
    original_type = backend.type_text
    def type_text(index, text, *, clear=True):
        dispatched.append(text)
        return original_type(index, text, clear=clear)
    backend.type_text = type_text
    state_reads = []
    original_state = backend.state
    def state():
        state_reads.append(True)
        return original_state()
    backend.state = state
    def batch(request):
        payload = json.loads(next(m for m in reversed(request.messages) if m.role is MessageRole.TOOL).content[0].text)
        revision = payload["data"]["state_revision"]
        arguments = {"browser_id": browser.browser_id, "state_revision": revision, "index": 1}
        return ModelResponse(tool_calls=(
            ToolCall("type-A", "browser_type", {**arguments, "text": "A"}),
            ToolCall("type-B-stale", "browser_type", {**arguments, "text": "B"}),
            ToolCall("click-stale", "browser_click", arguments),
        ))
    def replan(request):
        result = next(m for m in reversed(request.messages) if m.role is MessageRole.TOOL)
        assert result.tool_call_id == "click-stale"
        payload = json.loads(result.content[0].text)
        assert payload["data"]["effect"] == "not_executed"
        assert payload["data"]["error_code"] == "stale_observation"
        assert "value='A'" in result.content[-1].text
        return ModelResponse(tool_calls=(ToolCall("type-B-fresh", "browser_type", {
            "browser_id": browser.browser_id, "state_revision": payload["data"]["state_revision"],
            "index": 1, "text": "B"}),))
    platform = Scripted([
        ModelResponse(tool_calls=(ToolCall("read", "browser_state", {"browser_id": browser.browser_id}),)),
        batch, replan, ModelResponse(text="Requested input completed."),
    ])
    runtime.platform = platform
    try:
        result = runtime.start_turn(session.session_id, "Enter A, then B using the current page")
        assert result.status is AgentStatus.COMPLETED
        assert dispatched == ["A", "B"]
        assert len(state_reads) == 1  # initial observation only, no revision churn on conflicts
        failures = [e for e in runtime.store.events(session.session_id) if e.kind is AgentEventKind.TOOL_FAILED]
        assert [e.data["call_id"] for e in failures] == ["type-B-stale", "click-stale"]
        assert all(e.data["data"]["execution_status"] == "not_executed" for e in failures)
        assert json.loads(runtime.get_session(session.session_id).messages[-2].content)["data"]["effect"] == "changed"
    finally:
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

    messages, extra = runtime._prepare_model_request(session, _step(runtime, session), None)
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

    messages, extra = runtime._prepare_model_request(session, _step(runtime, session), None)
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
