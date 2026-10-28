"""A small question stays small: prompt migration, stopped-turn marker, scratch files, ignored probes.

Field case (2026-10-28): "where can I buy a Vietnam exit IP" in a long proxy thread became a
10-minute SSH investigation. The session was frozen on an early default prompt, the previous
turn had been stopped mid-investigation with nothing in history saying so, and probe scripts
written under a gitignored folder were then demanded acceptance checks.
"""
import subprocess

import pytest

from app.ai import ModelResponse, ToolCall
from app.agent_runtime.contracts import AgentSession, AgentStatus
from app.agent_runtime.runtime import AgentRuntime
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.system_prompts import (
    DEFAULT_AGENT_SYSTEM_PROMPT,
    DEFAULT_AGENT_SYSTEM_PROMPT_VERSION,
    _LEGACY_DEFAULT_AGENT_SYSTEM_PROMPTS,
    _V9_DEFAULT_AGENT_SYSTEM_PROMPT,
    _V13_DEFAULT_AGENT_SYSTEM_PROMPT,
    is_loom_default_prompt,
)
from test_development_feedback import make_runtime


_SESSION_ID = "00000000-0000-0000-0000-000000000002"
_PARAGRAPHS = _V9_DEFAULT_AGENT_SYSTEM_PROMPT.split("\n\n")
# The early defaults found frozen on disk: first paragraph only, the first three, and those
# three plus the image paragraph (340, 1,111 and 1,572 characters).
_EARLY_DEFAULTS = (
    _PARAGRAPHS[0],
    "\n\n".join(_PARAGRAPHS[:3]),
    "\n\n".join((*_PARAGRAPHS[:3], _PARAGRAPHS[-1])),
)


class _Platform:
    pass


def _stored(tmp_path, prompt, *, version=0):
    store = FileAgentSessionStore(tmp_path / "state")
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    store.create(AgentSession(session_id=_SESSION_ID, profile_id="agent.fast", system_prompt=prompt,
                              system_prompt_version=version, workspace_dir=str(workspace),
                              created_at="2026-09-20T00:00:00Z", updated_at="2026-09-20T00:00:00Z"))
    return store, AgentRuntime(platform=_Platform(), store=store)


@pytest.mark.parametrize("early", _EARLY_DEFAULTS, ids=["one-paragraph", "three-paragraphs", "with-image"])
def test_early_defaults_missing_from_snapshots_are_upgraded(tmp_path, early) -> None:
    assert early not in _LEGACY_DEFAULT_AGENT_SYSTEM_PROMPTS
    store, runtime = _stored(tmp_path, early)
    loaded = runtime.get_session(_SESSION_ID)
    assert loaded.system_prompt == DEFAULT_AGENT_SYSTEM_PROMPT
    assert store.load(_SESSION_ID).system_prompt_version == DEFAULT_AGENT_SYSTEM_PROMPT_VERSION


@pytest.mark.parametrize("prompt", [
    DEFAULT_AGENT_SYSTEM_PROMPT + "\n\nYou are a worker sub-agent; report results rather than pretending the parent already knows them.",
    _PARAGRAPHS[0] + "\n\nAlways answer in French.",
    "CUSTOM PROJECT AGENT PROMPT",
])
def test_prompts_with_any_line_loom_never_wrote_stay_custom(tmp_path, prompt) -> None:
    assert not is_loom_default_prompt(prompt)
    _, runtime = _stored(tmp_path, prompt)
    assert runtime.get_session(_SESSION_ID).system_prompt == prompt


def test_v14_keeps_a_question_its_own_size_and_v13_still_migrates() -> None:
    assert "Match the work to the request." in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "Earlier work that was stopped or left unfinished is background" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "scratch_dir in LOOM_RUNTIME_STATE" in DEFAULT_AGENT_SYSTEM_PROMPT
    assert "artifacts/result.png" not in DEFAULT_AGENT_SYSTEM_PROMPT
    assert _V13_DEFAULT_AGENT_SYSTEM_PROMPT in _LEGACY_DEFAULT_AGENT_SYSTEM_PROMPTS
    assert is_loom_default_prompt(_V13_DEFAULT_AGENT_SYSTEM_PROMPT)


class _Scripted:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def execute_chat(self, profile, request):
        self.requests.append(request)
        return self.responses.pop(0)


def _stopped_notes(request):
    return [m for m in request.messages if m.name == "loom_turn_stopped"]


def test_next_request_says_the_stopped_turn_is_background(tmp_path) -> None:
    class Cancelling(_Scripted):
        def execute_chat(self, profile, request):
            if not self.requests:
                self.requests.append(request)
                runtime.cancel(session.session_id)
                return ModelResponse(text="checking your proxy servers")
            return super().execute_chat(profile, request)

    platform = Cancelling(ModelResponse(text="Here are three providers."))
    runtime, session, _ = make_runtime(tmp_path, platform)
    try:
        assert runtime.start_turn(session.session_id, "why can't I reach the NY IP").status is AgentStatus.CANCELLED
        assert not _stopped_notes(platform.requests[0])
        result = runtime.start_turn(session.session_id, "where can I buy a Vietnam exit IP")
        assert result.status is AgentStatus.COMPLETED
        notes = _stopped_notes(platform.requests[1])
        assert len(notes) == 1 and "was stopped by the user" in notes[0].content
        names = [m.name for m in platform.requests[1].messages]
        user_index = next(i for i, m in enumerate(platform.requests[1].messages)
                          if m.role.value == "user" and m.content == "where can I buy a Vietnam exit IP")
        assert names.index("loom_turn_stopped") < user_index
        # A completed turn leaves nothing behind for the turn after it.
        platform.responses.append(ModelResponse(text="ok"))
        runtime.start_turn(session.session_id, "thanks")
        assert len(_stopped_notes(platform.requests[2])) == 1  # the old note stays where it was, no new one
    finally:
        runtime.close()


def test_scratch_files_stay_out_of_the_project_diff_and_the_gate(tmp_path) -> None:
    platform = _Scripted(
        ModelResponse(tool_calls=(ToolCall("w", "write_workspace_text", {"path": "scratch:probe.py", "text": "print(1)"}),
                                  ToolCall("r", "read_workspace_text", {"path": "scratch:probe.py"}),
                                  ToolCall("x", "write_workspace_text", {"path": "scratch:../escape.py", "text": "x"}))),
        ModelResponse(text="Done"),
    )
    runtime, session, workspace = make_runtime(tmp_path, platform)
    try:
        result = runtime.start_turn(session.session_id, "probe something")
        assert result.status is AgentStatus.COMPLETED
        turn_id = runtime.store.load(session.session_id).current_turn_id
        scratch = runtime.store.session_dir(session.session_id) / "run-artifacts" / turn_id
        assert (scratch / "probe.py").read_text() == "print(1)"
        assert not (scratch.parent / "escape.py").exists()
        assert list(workspace.iterdir()) == []
        tools = [m for m in platform.requests[1].messages if m.role.value == "tool"]
        assert '"content":"print(1)"' in tools[1].content
        assert "escapes the scratch directory" in tools[2].content
        assert runtime.diff_trackers.snapshot(session.session_id, turn_id).paths == ()
        assert not [m for m in platform.requests[1].messages if m.name == "loom_development_verification"]
        state = next(m for m in platform.requests[0].messages if m.name == "loom_runtime_state")
        assert '"scratch_dir"' in state.content and turn_id in state.content and "scratch:probe.py" in state.content
    finally:
        runtime.close()


def test_gitignored_probe_is_not_a_source_change_but_project_source_is(tmp_path) -> None:
    def write(call, path):
        return ModelResponse(tool_calls=(ToolCall(call, "write_workspace_text", {"path": path, "text": "print(1)"}),))

    platform = _Scripted(write("probe", "artifacts/vn_auth.py"), ModelResponse(text="Done"),
                         write("real", "app/main.py"), ModelResponse(text="Done"),
                         ModelResponse(text="Done"), ModelResponse(text="Done"))
    runtime, session, workspace = make_runtime(tmp_path, platform)
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    (workspace / ".gitignore").write_text("/artifacts/\n")
    try:
        runtime.start_turn(session.session_id, "probe the proxy")
        assert not [m for m in platform.requests[1].messages if m.name == "loom_development_verification"]
        runtime.start_turn(session.session_id, "now fix the app")
        gate = [m for m in platform.requests[3].messages if m.name == "loom_development_verification"]
        assert gate and '"app/main.py"' in gate[-1].content and "vn_auth.py" not in gate[-1].content
        assert "Execute the missing verification now" not in gate[-1].content
    finally:
        runtime.close()
