"""A tool call item carries the model step it came from, like the assistant item does."""
from app.agent_runtime.contracts import AgentEvent, AgentEventKind
from app.app_server import _apply_event_to_item


def event(kind, **data):
    return AgentEvent(event_id="e", session_id="s", turn_id="t", kind=kind, created_at="2026-10-09T00:00:00Z", data=data)


def test_tool_item_records_the_step_that_requested_it():
    item = {}
    _apply_event_to_item(item, event(AgentEventKind.TOOL_REQUESTED, call_id="c1", tool="update_plan", step_id="step-7", arguments={}))
    assert item["stepId"] == "step-7"
    _apply_event_to_item(item, event(AgentEventKind.TOOL_COMPLETED, call_id="c1", tool="update_plan", ok=True, content="", data={}))
    assert item["stepId"] == "step-7" and item["status"] == "completed"


def test_assistant_and_tool_items_of_one_step_share_its_identity():
    reply, tool = {}, {}
    _apply_event_to_item(reply, event(AgentEventKind.MODEL_RESPONSE, text="Stage done", step_id="step-9", phase="commentary"))
    _apply_event_to_item(tool, event(AgentEventKind.TOOL_REQUESTED, call_id="c2", tool="update_plan", step_id="step-9", arguments={}))
    assert reply["stepId"] == tool["stepId"] == "step-9"


def test_a_tool_request_without_a_step_id_stays_unlinked():
    item = {}
    _apply_event_to_item(item, event(AgentEventKind.TOOL_REQUESTED, call_id="c3", tool="exec", arguments={}))
    assert item["stepId"] is None
