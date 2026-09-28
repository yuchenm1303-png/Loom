from __future__ import annotations

import threading
import time

from app.agent_runtime import AgentEventKind, AgentRuntime, AgentStatus, FileAgentSessionStore, PermissionMode
from app.agent_runtime.patch_tools import apply_patch_tool
from app.agent_runtime.process_tools import exec_tool
from app.agent_runtime.tools import AgentTool, ToolRegistry, ToolResult
from app.ai import AGENT_FAST_ROLE, MessageRole, ModelResponse, ToolCall


class ScriptedPlatform:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, _profile_id, request):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("scripted platform ran out of responses")
        return self.responses.pop(0)


def _parallel_probe(state, both_started):
    def handler(_context, arguments):
        label = str(arguments["label"])
        with state["lock"]:
            state["running"] += 1
            state["max_running"] = max(state["max_running"], state["running"])
            state["timeline"].append(("start", label))
            if state["running"] >= 2:
                both_started.set()
        assert both_started.wait(1.5), "parallel-safe calls never overlapped"
        time.sleep(0.08 if label == "a" else 0.01)
        with state["lock"]:
            state["timeline"].append(("finish", label))
            state["running"] -= 1
        return ToolResult(ok=True, content=f"result:{label}")

    return AgentTool(
        name="parallel_probe",
        description="A test probe that may execute concurrently.",
        input_schema={
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
            "additionalProperties": False,
        },
        handler=handler,
        supports_parallel_tool_calls=True,
    )


def _serial_probe(timeline):
    def handler(_context, arguments):
        label = str(arguments["label"])
        timeline.append(("start", label))
        time.sleep(0.02)
        timeline.append(("finish", label))
        return ToolResult(ok=True, content=f"result:{label}")

    return AgentTool(
        name="serial_probe",
        description="A test probe that is an ordering barrier.",
        input_schema={
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
            "additionalProperties": False,
        },
        handler=handler,
    )


def test_parallel_tools_overlap_but_model_history_keeps_call_order(tmp_path):
    state = {"lock": threading.Lock(), "running": 0, "max_running": 0, "timeline": []}
    both_started = threading.Event()
    platform = ScriptedPlatform([
        ModelResponse(tool_calls=(
            ToolCall(call_id="a", name="parallel_probe", arguments={"label": "a"}),
            ToolCall(call_id="b", name="parallel_probe", arguments={"label": "b"}),
        )),
        ModelResponse(text="done"),
    ])
    store = FileAgentSessionStore(tmp_path / "state")
    runtime = AgentRuntime(
        platform=platform,
        store=store,
        tools=ToolRegistry((_parallel_probe(state, both_started),)),
        max_parallel_tools=4,
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.READ_ONLY,
    )

    result = runtime.start_turn(session.session_id, "Inspect both independently.")

    assert result.status is AgentStatus.COMPLETED
    assert state["max_running"] == 2
    assert state["timeline"].index(("finish", "b")) < state["timeline"].index(("finish", "a"))

    saved = runtime.get_session(session.session_id)
    tool_messages = [message for message in saved.messages if message.role is MessageRole.TOOL]
    assert [message.tool_call_id for message in tool_messages[-2:]] == ["a", "b"]

    completed = [
        event for event in store.events(session.session_id)
        if event.kind is AgentEventKind.TOOL_COMPLETED
    ]
    assert [event.data["call_id"] for event in completed[-2:]] == ["b", "a"]
    assert len({event.data["parallel_batch_id"] for event in completed[-2:]}) == 1
    runtime.close()


def test_nonparallel_tool_is_an_exclusive_ordering_barrier(tmp_path):
    state = {"lock": threading.Lock(), "running": 0, "max_running": 0, "timeline": []}
    first_pair_started = threading.Event()
    parallel = _parallel_probe(state, first_pair_started)
    serial = _serial_probe(state["timeline"])
    platform = ScriptedPlatform([
        ModelResponse(tool_calls=(
            ToolCall(call_id="a1", name="parallel_probe", arguments={"label": "a"}),
            ToolCall(call_id="a2", name="parallel_probe", arguments={"label": "b"}),
            ToolCall(call_id="barrier", name="serial_probe", arguments={"label": "barrier"}),
        )),
        ModelResponse(text="done"),
    ])
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry((parallel, serial)),
        max_parallel_tools=4,
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.READ_ONLY,
    )

    result = runtime.start_turn(session.session_id, "Run the independent reads, then the barrier.")

    assert result.status is AgentStatus.COMPLETED
    timeline = state["timeline"]
    assert timeline.index(("finish", "a")) < timeline.index(("start", "barrier"))
    assert timeline.index(("finish", "b")) < timeline.index(("start", "barrier"))
    runtime.close()


def test_parallel_capability_is_explicit_and_mutations_stay_serial():
    assert AgentTool(
        name="default_probe",
        description="Default serial probe.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        handler=lambda _context, _arguments: ToolResult(ok=True, content="ok"),
    ).supports_parallel_tool_calls is False

    assert exec_tool().supports_parallel_tool_calls is True
    assert apply_patch_tool().supports_parallel_tool_calls is False


def test_agent_requests_parallel_tool_calls_from_the_model(tmp_path):
    platform = ScriptedPlatform([ModelResponse(text="done")])
    runtime = AgentRuntime(
        platform=platform,
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=ToolRegistry(),
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    session = runtime.create_session(
        AGENT_FAST_ROLE.role_id,
        workspace_dir=workspace,
        permission_mode=PermissionMode.READ_ONLY,
    )

    runtime.start_turn(session.session_id, "Hello")

    assert platform.requests
    assert platform.requests[0].parallel_tool_calls is True
    runtime.close()
