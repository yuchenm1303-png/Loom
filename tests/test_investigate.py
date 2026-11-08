"""investigate: a read-only sub-agent answers a question; only its answer enters the asker's history."""
import json
import threading

import pytest

from app.agent_runtime import investigate
from app.agent_runtime.contracts import AgentLimits, AgentStatus, PermissionMode
from app.agent_runtime.multi_agent_runtime import MultiAgentRuntime
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext, ToolRegistry
from app.agent_runtime.workspace_tools import loom_default_tools
from app.ai import ModelResponse, ModelUsage, ToolCall

SECRET = "第七行的配置写在 settings.ini 里，只有调查员读过这一行"
QUESTION = "配置是在哪个文件里读出来的？我要改它的默认值。"


def usage(total=10):
    return ModelUsage(input_tokens=total, output_tokens=0, total_tokens=total, cached_input_tokens=0)


def answer(text, total=10):
    return ModelResponse(text=text, usage=usage(total))


def calling(name, arguments, call_id="call-1", total=10):
    return ModelResponse(tool_calls=(ToolCall(call_id=call_id, name=name, arguments=arguments),), usage=usage(total))


class Platform:
    """Scripted model per conversation: the investigator answers from its script, the asker from its own."""

    def __init__(self, lead, investigator):
        self.lead, self.investigator, self.requests, self.lock = list(lead), list(investigator), [], threading.Lock()

    def execute_chat(self, _profile, request):
        system = " ".join(str(message.content) for message in request.messages if message.role.value == "system")
        said = " ".join(str(message.content) for message in request.messages)
        with self.lock:
            self.requests.append(request)
            mine = "read-only investigator" in system or "Question:\n" in said
            item = (self.investigator if mine else self.lead).pop(0)
        if isinstance(item, threading.Event):
            item.wait(10)
            return answer("等到了")
        return item


@pytest.fixture
def setup(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("import settings\n" * 6 + SECRET + "\n", encoding="utf-8")
    runtimes = []

    def make(lead, investigator=(), tools=()):
        platform = Platform(lead, investigator)
        registry = loom_default_tools()
        for tool in tools:
            registry.register(tool)
        runtime = MultiAgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "home"),
                                    tools=registry, default_permission_mode=PermissionMode.WORKSPACE,
                                    auto_drain_queue=False)
        runtime.limits = AgentLimits(transport_retries=0, model_retries=0)
        runtimes.append(runtime)
        session = runtime.create_session("default", workspace_dir=workspace)
        return runtime, platform, session.session_id
    yield make
    for runtime in runtimes:
        runtime.close()


def lead_requests(platform):
    return [request for request in platform.requests
            if "read-only investigator" not in " ".join(str(m.content) for m in request.messages if m.role.value == "system")]


def test_only_the_answer_enters_the_askers_history(setup):
    runtime, platform, source = setup(
        [calling("investigate", {"question": QUESTION, "paths": ["app.py"]}), answer("知道了")],
        [calling("read_workspace_text", {"path": "app.py"}), answer("答案：配置在 settings.ini，读取在 app.py:1。")])
    runtime.start_turn(source, "改配置默认值")
    lead = json.dumps([str(m.content) for m in lead_requests(platform)[-1].messages], ensure_ascii=False)
    assert "答案：配置在 settings.ini" in lead and "1 reads" in lead
    assert SECRET not in lead, "what the investigator read stays in its own context"
    [node] = runtime.agent_control.list_tree(source)
    child = runtime.store.load(node.node.session_id)
    assert node.node.relation_status.value == "closed" and node.node.history_mode.value == "none"
    assert child.permission_mode is PermissionMode.READ_ONLY, "read-only is the permission mode, not a request"
    assert set(child.tool_selection) == set(investigate.READ_TOOLS)
    assert child.system_prompt == investigate.SYSTEM
    investigator_request = [r for r in platform.requests if r not in lead_requests(platform)][0]
    offered = {tool.name for tool in investigator_request.tools}
    assert offered <= set(investigate.READ_TOOLS) and "read_workspace_text" in offered
    assert "write_workspace_text" not in offered and "exec" not in offered and "spawn_agent" not in offered


def test_an_investigator_cannot_change_anything(setup, tmp_path):
    runtime, platform, source = setup(
        [calling("investigate", {"question": QUESTION}), answer("好")],
        [calling("write_workspace_text", {"path": "app.py", "content": "改掉"}), answer("我没法改")])
    runtime.start_turn(source, "查一下")
    assert SECRET in (tmp_path / "workspace" / "app.py").read_text(encoding="utf-8")
    [node] = runtime.agent_control.list_tree(source)
    events = runtime.store.events(node.node.session_id)
    assert not any(e.kind.value == "tool_completed" and e.data.get("tool") == "write_workspace_text" for e in events)


def test_over_its_limit_it_is_asked_to_answer_from_what_it_has(setup, monkeypatch):
    monkeypatch.setattr(investigate, "TOKEN_CAP", 0)
    gate = threading.Event()
    runtime, platform, source = setup(
        [calling("investigate", {"question": QUESTION}), answer("好")], [gate, answer("从已有的看：在 app.py")])
    original = runtime.cancel
    monkeypatch.setattr(runtime, "cancel", lambda session_id: (gate.set(), original(session_id))[1])
    runtime.start_turn(source, "查一下")
    said = json.dumps([str(m.content) for r in platform.requests for m in r.messages], ensure_ascii=False)
    assert investigate.ANSWER_NOW[:40] in said
    assert "从已有的看：在 app.py" in json.dumps([str(m.content) for m in lead_requests(platform)[-1].messages], ensure_ascii=False)


def test_a_cancelled_asker_cancels_and_closes_its_investigation(setup):
    gate = threading.Event()
    runtime, platform, source = setup([], [gate])
    tool = runtime.tools.get("investigate")
    context = ToolContext(session_id=source, turn_id="t", workspace=runtime.store.load(source).workspace_dir,
                          is_cancelled=lambda: True)
    try:
        with pytest.raises(RuntimeError, match="cancellation"):
            tool.handler(context, {"question": QUESTION})
        [node] = runtime.agent_control.list_tree(source)
        assert node.node.relation_status.value == "closed"
    finally:
        gate.set()


def test_no_answer_is_a_failed_call_that_says_what_to_do(setup):
    runtime, platform, source = setup([calling("investigate", {"question": QUESTION}), answer("好")], [answer("")])
    runtime.start_turn(source, "查一下")
    said = json.dumps([str(m.content) for m in lead_requests(platform)[-1].messages], ensure_ascii=False)
    assert "gave no answer" in said


@pytest.mark.parametrize("arguments,expected", [({"question": "在哪"}, "complete question")])
def test_a_vague_question_is_refused(setup, arguments, expected):
    runtime, _, source = setup([])
    context = ToolContext(session_id=source, turn_id="t", workspace=runtime.store.load(source).workspace_dir)
    with pytest.raises(ValueError, match=expected):
        runtime.tools.get("investigate").handler(context, arguments)


def test_an_investigator_cannot_start_another(setup):
    runtime, _, source = setup([])
    node = runtime.agent_control.graph.add_child(parent_session_id=source, child_session_id=runtime.create_session(
        "default").session_id, role=investigate.ROLE, history_mode="none")
    context = ToolContext(session_id=node.session_id, turn_id="t", workspace=runtime.store.load(source).workspace_dir)
    with pytest.raises(ValueError, match="cannot start another"):
        runtime.tools.get("investigate").handler(context, {"question": QUESTION})


def reads(count, start=0):
    return [calling("read_workspace_text", {"path": "app.py"}, f"call-r{start + n}") for n in range(count)]


def held(runtime, source):
    return [event.data for event in runtime.store.events(source)
            if event.kind.value == "tool_failed" and "Not executed, once" in str(event.data.get("content"))]


def ran(runtime, source):
    return sum(1 for event in runtime.store.events(source)
               if event.kind.value == "tool_completed" and event.data.get("tool") == "read_workspace_text")


@pytest.fixture
def large(monkeypatch):
    monkeypatch.setattr(investigate, "HOLD_CONTEXT", 0)  # as if the conversation were already large
    monkeypatch.setattr(investigate, "_in_long_goal", lambda runtime, session: True)  # and in a long goal


def test_a_large_conversation_reading_a_lot_is_held_once(setup, large):
    after = investigate.HOLD_AFTER
    runtime, platform, source = setup([*reads(after), *reads(2, 100), answer("读完了")])
    runtime.start_turn(source, "读一读")
    assert len(held(runtime, source)) == 1, "the read after the tenth, once"
    assert ran(runtime, source) == after + 1, "repeating it runs it"
    said = json.dumps([str(m.content) for m in platform.requests[-1].messages], ensure_ascii=False)
    assert "call investigate with the question" in said


def test_only_a_long_goal_is_held(setup, monkeypatch):
    # beam-free, held in a plain one-question turn: 39% less for the asker, three times as much in all.
    monkeypatch.setattr(investigate, "HOLD_CONTEXT", 0)
    runtime, platform, source = setup([*reads(investigate.HOLD_AFTER + 2), answer("一"),
                                       *reads(investigate.HOLD_AFTER), *reads(2, 200), answer("二")])
    runtime.start_turn(source, "普通对话")
    assert not held(runtime, source), "no goal: never held"
    runtime.set_goal(source, "整晚研究")
    runtime.start_turn(source, "长目标里")
    assert len(held(runtime, source)) == 1


def test_a_small_conversation_is_never_held(setup):
    runtime, platform, source = setup([*reads(investigate.HOLD_AFTER + 3), answer("读完了")])
    runtime.start_turn(source, "读一读")
    assert not held(runtime, source) and ran(runtime, source) == investigate.HOLD_AFTER + 3


def test_no_hold_once_the_turn_has_investigated(setup, large):
    runtime, platform, source = setup(
        [calling("investigate", {"question": QUESTION}, "call-i"), *reads(investigate.HOLD_AFTER + 2), answer("好")],
        [answer("答案：app.py:7")])
    runtime.start_turn(source, "读一读")
    assert not held(runtime, source)


def test_only_the_reads_of_a_mixed_batch_are_held(setup, large, tmp_path):
    mixed = ModelResponse(tool_calls=(ToolCall(call_id="call-m1", name="read_workspace_text", arguments={"path": "app.py"}),
                                      ToolCall(call_id="call-m2", name="write_workspace_text", arguments={"path": "m.txt", "text": "x"})),
                          usage=usage())
    runtime, platform, source = setup([*reads(investigate.HOLD_AFTER), mixed, answer("好")])
    session = runtime.store.load(source)
    session.permission_mode = PermissionMode.FULL_ACCESS
    runtime.store.save(session)
    runtime.start_turn(source, "读完再写")
    assert [item.get("tool") for item in held(runtime, source)] == ["read_workspace_text"]
    assert (tmp_path / "workspace" / "m.txt").exists(), "the write beside it ran"


def test_the_count_starts_again_each_turn(setup, large):
    runtime, platform, source = setup([*reads(investigate.HOLD_AFTER - 1), answer("一"), *reads(2, 50), answer("二")])
    runtime.start_turn(source, "第一轮")
    runtime.start_turn(source, "第二轮")
    assert not held(runtime, source)


def test_what_is_not_reading_is_not_held(setup, large, tmp_path):
    writes = [calling("write_workspace_text", {"path": f"n{n}.txt", "text": "x"}, f"call-w{n}") for n in range(2)]
    runtime, platform, source = setup([*reads(investigate.HOLD_AFTER), *writes, answer("好")])
    session = runtime.store.load(source)
    session.permission_mode = PermissionMode.FULL_ACCESS
    runtime.store.save(session)
    runtime.start_turn(source, "读完再写")
    assert not held(runtime, source) and (tmp_path / "workspace" / "n1.txt").exists()


@pytest.mark.parametrize("argv,reading", [
    # Real run: the lead read with these, so a count of the workspace tools alone missed most of its reading.
    (["powershell", "-NoProfile", "-Command", "Get-Content -LiteralPath 'app/goal.py' | Select-Object -Skip 40"], True),
    (["powershell", "-Command", "Select-String -Path app/*.py -Pattern budget"], True),
    (["rg", "-n", "tokenBudget", "app"], True),
    (["cmd", "/c", "git log --oneline -5"], True),
    (["powershell", "-Command", "Get-Content a.txt | Set-Content b.txt"], False),
    (["python", "-m", "pytest", "-q"], False),
    (["cmd", "/c", "type a.txt > b.txt"], False),
])
def test_commands_that_read_count_as_reading(argv, reading):
    assert investigate._is_read(ToolCall(call_id="c", name="exec", arguments={"argv": argv})) is reading


def test_a_sub_agent_reading_is_not_held(setup, large):
    runtime, platform, source = setup([], [])
    child = runtime.agent_control.spawn(source, "Question:\n读很多文件", role="worker", history_mode="none", background=False)
    assert child.session_status is not AgentStatus.RUNNING
    platform.investigator = [*reads(investigate.HOLD_AFTER + 2), answer("好")]
    runtime.start_turn(child.node.session_id, "Question:\n再读")
    assert not held(runtime, child.node.session_id)
    assert ran(runtime, child.node.session_id) == investigate.HOLD_AFTER + 2
