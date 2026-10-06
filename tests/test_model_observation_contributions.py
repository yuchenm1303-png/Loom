"""Observation contributors cannot assemble or rewrite canonical request history."""
from types import SimpleNamespace as NS

from app.ai import AIMessage, ImagePart, MessageRole
from app.agent_runtime.browser_runtime_v1 import BrowserRuntime
from app.agent_runtime.computer_single_loop_runtime import SingleLoopComputerRuntime
from app.agent_runtime.tool_observation import attach_observation


def test_observation_attachment_preserves_old_tool_history():
    old = AIMessage(role=MessageRole.TOOL, name="browser_state", tool_call_id="call-1", content="original")
    result = attach_observation([old], "fresh external snapshot", tool_prefix="browser_",
                                tool_call_id="call-1", image=ImagePart("data:image/png;base64,AA=="))
    assert result[0] is old
    assert result[0].content == "original"
    assert [m.name for m in result[1:]] == ["loom_tool_observation_text", "loom_tool_observation"]
    assert "call-1" in result[1].content[0].text


def test_browser_and_computer_are_contributors_not_final_request_builders():
    assert "_prepare_model_request" not in vars(BrowserRuntime)
    assert "_prepare_model_request" not in vars(SingleLoopComputerRuntime)
    assert "_collect_model_observations" in vars(BrowserRuntime)
    assert "_collect_model_observations" in vars(SingleLoopComputerRuntime)


def test_browser_security_contract_is_stable_without_feedback(monkeypatch):
    from app.agent_runtime.browser_runtime_v1 import _BrowserRuntime, _BROWSER_UNTRUSTED_SYSTEM_CONTRACT, _BROWSER_ACTION_CONTRACT
    monkeypatch.setattr(_BrowserRuntime, "_request_stable_contracts", lambda _s: ("base",), raising=False)
    runtime = object.__new__(BrowserRuntime)
    assert runtime._request_stable_contracts() == ("base", _BROWSER_UNTRUSTED_SYSTEM_CONTRACT + _BROWSER_ACTION_CONTRACT)


def test_browser_resource_receipt_is_contribution_not_system_instruction(monkeypatch):
    from app.agent_runtime.browser_runtime_v1 import _BrowserRuntime
    from app.agent_runtime.contracts import AgentEventKind
    monkeypatch.setattr(_BrowserRuntime, "_collect_model_observations", lambda *_a: ([], {}), raising=False)
    runtime = object.__new__(BrowserRuntime)
    event = NS(turn_id="previous", kind=AgentEventKind.TURN_FAILED, created_at="yesterday",
               data={"browser_resources": {"browser_ids": ["released-browser"]}})
    runtime.store = NS(events=lambda _s: [event])
    runtime._browser_feedback_turns = {}
    runtime.browser_sessions = None
    event.event_id = "released-event"
    messages, metadata = runtime._collect_model_observations(NS(session_id="s", current_turn_id="now", request_context_frames=[]), NS())
    assert metadata == {"last_browser_release_event_id": "released-event"}
    assert len(messages) == 1
    assert messages[0].role is MessageRole.USER
    assert messages[0].name == "loom_resource_resume"
    assert "released-browser" in messages[0].content


def test_computer_observation_contributes_once_with_source_identity(monkeypatch):
    from app.agent_runtime.computer_single_loop_runtime import ComputerUseRuntime
    monkeypatch.setattr(ComputerUseRuntime, "_collect_model_observations", lambda *_a: ([], {}), raising=False)
    monkeypatch.setattr("app.agent_runtime.computer_single_loop_runtime._model_observation_text", lambda _s: "snapshot")
    runtime = object.__new__(SingleLoopComputerRuntime)
    image = NS(image_data=b"png", image_media_type="image/png", image_data_url=lambda: "data:image/png;base64,AA==",
               frame=NS(width=5, height=7), windows=(), controls=())
    runtime.computer_sessions = NS(latest=lambda _s: NS(observation=image, state_revision=4))
    runtime._computer_feedback_turns = {"s": "t"}
    old = AIMessage(role=MessageRole.TOOL, name="computer_action", tool_call_id="computer-1", content="original")
    session = NS(session_id="s", current_turn_id="t", messages=[old])
    contributions, metadata = runtime._collect_model_observations(session, NS())
    assert [m.name for m in contributions] == ["loom_tool_observation_text", "loom_tool_observation"]
    assert "computer-1" in contributions[0].content[0].text
    assert metadata["computer_observation"]["source_call_id"] == "computer-1"
    assert session.messages == [old]
    assert runtime._collect_model_observations(session, NS()) == ([], {})
