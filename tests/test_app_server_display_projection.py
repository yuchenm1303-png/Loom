from app.agent_runtime import AgentEvent, AgentEventKind
from app.app_server import _apply_event_to_item, _usage_record
from types import SimpleNamespace


def test_display_item_keeps_raw_text_and_cached_usage():
    event = AgentEvent(event_id="e", session_id="s", turn_id="t",
                       kind=AgentEventKind.MODEL_RESPONSE, created_at="now",
                       data={"text":"Raw", "display_text":"Raw[[AI_LEDGER_INLINE_STICKER:soft_smile]]",
                             "display_runtime_authored":True,
                             "usage":{"input_tokens":10,"cached_input_tokens":7}})
    item = {}
    _apply_event_to_item(item, event)
    assert item["text"] == event.data["display_text"]
    assert item["rawText"] == "Raw"
    assert item["displayRuntimeAuthored"] is True
    assert item["usage"]["cachedInputTokens"] == 7
    assert _usage_record(SimpleNamespace(input_tokens=10,cached_input_tokens=7))["cachedInputTokens"] == 7


def test_thread_read_and_completed_notification_use_identical_display(tmp_path):
    from app.agent_runtime import AgentRuntime, FileAgentSessionStore, SandboxManager, SandboxPolicy
    from app.app_server_streaming import StreamingLoomAppServerService
    from app.ai import ModelResponse
    class Platform:
        def execute_chat(self, _profile, _request):
            return ModelResponse(text="The requested work is complete and the result has been verified.")
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(platform=Platform(), store=store,
                           sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF))
    service = StreamingLoomAppServerService(runtime=runtime, store=store, model="test", default_workspace=tmp_path)
    observed = []
    service.subscribe_notifications(lambda method, params: observed.append((method, params)))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=tmp_path)
        runtime.start_turn(session.session_id, "Please finish the work")
        snapshot = service.thread_read({"threadId":session.session_id})
        completed = [params["item"] for method, params in observed
                     if method == "item/completed" and params["item"]["type"] == "assistant_message"][-1]
        durable = [item for item in snapshot["turns"][-1]["items"] if item["type"] == "assistant_message"][-1]
        assert completed["text"] == durable["text"] == snapshot["finalText"]
        assert "[[AI_LEDGER_INLINE_STICKER:" in completed["text"]
        assert completed["rawText"] == durable["rawText"] == snapshot["rawFinalText"]
        turn = [params["turn"] for method, params in observed if method == "turn/completed"][-1]
        assert turn["finalText"] == completed["text"]
        assert turn["rawFinalText"] == completed["rawText"]
        assert turn["displayRuntimeAuthored"] is True
    finally:
        runtime.close()

def test_plan_tool_item_preserves_structured_outcome_and_evidence_refs():
    plan = [{"step":"Pressure","status":"completed","outcome":"interrupted",
             "evidence_refs":[{"call_id":"executed-17"},{"path":"reports/result.json"}]}]
    event = AgentEvent(event_id="e",session_id="s",turn_id="t",kind=AgentEventKind.TOOL_COMPLETED,
                       created_at="now",data={"ok":True,"tool":"update_plan","data":{"plan":plan}})
    item = {}
    _apply_event_to_item(item,event)
    assert item["result"]["plan"] == plan
