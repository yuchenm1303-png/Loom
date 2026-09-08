"""Why the agent answers questions about the machine instead of deflecting.

Asked how much RAM was free, the agent called `memory_status` -- Loom's own
long-term memory store -- and then told the user to open Task Manager. It was
routing by tool *name* over ~50 name-only entries, not hallucinating.

An A/B over the real provider isolated the cause. Adding a host `environment`
block to the runtime-state envelope did **not** change the behaviour: the model
simply name-matched `computer_status` instead. Adding one paragraph to the
system prompt did, with or without the envelope change. So the decisive fact is
that nothing ever told the agent that answering host questions by running a
command is part of its job.

Both changes are kept and pinned here: the prompt is what makes it reach for
`exec`, and the envelope is what lets it compose a command that suits the host.
"""

from __future__ import annotations

import json
import re

from app.agent_runtime import (
    PermissionMode,
    SandboxManager,
    SandboxPolicy,
    StepContext,
    build_world_state_envelope,
)
from app.agent_runtime.context_state import (
    RUNTIME_STATE_PREAMBLE,
    build_host_environment,
)
from app.agent_runtime.workspace_tools import loom_default_tools


def _step(workspace, *, step_id="step-1"):
    sandbox = SandboxManager(policy=SandboxPolicy.OFF).snapshot(
        permission_mode=PermissionMode.WORKSPACE,
        workspace=workspace,
    )
    return StepContext.build(
        step_id=step_id,
        session_id="session-1",
        turn_id="turn-1",
        model_step=1,
        workspace_dir=str(workspace),
        profile_id="agent.fast",
        permission_mode=PermissionMode.WORKSPACE,
        tool_router=loom_default_tools().router(),
        sandbox_snapshot=sandbox,
    )


def _envelope(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir(exist_ok=True)
    return build_world_state_envelope(_step(workspace))


def test_the_envelope_names_the_machine(tmp_path):
    environment = _envelope(tmp_path).payload["state"]["environment"]

    assert environment["platform"] in {"Windows", "macOS", "Linux"} or environment["platform"]
    assert environment["shell"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", environment["current_date"])
    assert re.fullmatch(r"[+-]\d{2}:\d{2}", environment["utc_offset"])


def test_the_shell_is_named_together_with_how_to_reach_it(tmp_path):
    environment = _envelope(tmp_path).payload["state"]["environment"]

    # exec takes argv and runs no implicit shell. Naming the shell without
    # saying that would trade one wrong belief for another.
    note = environment["shell_invocation"]
    assert "no implicit shell" in note
    assert "argv" in note
    assert "[" in note and "]" in note


def test_the_preamble_points_at_the_environment_before_restricting_inference():
    # Order matters: the model is told what it is running on, and only then
    # told not to assume more than that.
    assert RUNTIME_STATE_PREAMBLE.index("environment") < RUNTIME_STATE_PREAMBLE.index(
        "Do not infer"
    )
    assert "machine you are running on" in RUNTIME_STATE_PREAMBLE


def test_every_runtime_layer_renders_the_same_preamble(tmp_path):
    # MultiAgentRuntime used to re-render the envelope with its own edited copy
    # of this sentence, so edits to the original silently had no effect.
    from app.agent_runtime import multi_agent_runtime

    source = (
        __import__("pathlib")
        .Path(multi_agent_runtime.__file__)
        .read_text(encoding="utf-8")
    )
    assert "LOOM_RUNTIME_STATE v1" not in source
    assert "render_runtime_state_text" in source


def test_the_utc_offset_is_used_rather_than_a_localized_timezone_name():
    environment = build_host_environment()
    # A localized tzname arrives in the host code page and differs per machine
    # language, which is noise the model cannot rely on.
    assert "timezone" not in environment
    assert environment["utc_offset"].isascii()


def test_the_environment_stays_inside_the_hashed_state(tmp_path):
    payload = _envelope(tmp_path).payload
    state = payload["state"]
    assert "environment" in state

    # The digest covers state, so a host change is a state change.
    canonical = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert payload["state_digest"]
    assert "environment" in canonical


def test_the_prompt_makes_running_a_command_the_answer_to_host_questions():
    from app.agent_runtime.runtime import DEFAULT_AGENT_SYSTEM_PROMPT

    prompt = DEFAULT_AGENT_SYSTEM_PROMPT
    # The behaviour, not the wording: reach for exec rather than deflecting.
    assert "exec" in prompt
    assert "free" in prompt and "memory" in prompt
    assert "do not report" in prompt.casefold()
    assert "themselves" in prompt


def test_the_prompt_disambiguates_the_two_misleading_tool_names():
    from app.agent_runtime.runtime import DEFAULT_AGENT_SYSTEM_PROMPT

    prompt = DEFAULT_AGENT_SYSTEM_PROMPT
    # Both names describe Loom's internals and both were picked by name for a
    # question about the user's computer.
    assert "memory_status" in prompt
    assert "computer_status" in prompt
    assert "not by what they are called" in prompt


def test_the_prompt_points_at_the_envelope_for_platform_details():
    from app.agent_runtime.runtime import DEFAULT_AGENT_SYSTEM_PROMPT

    # The envelope carries platform and shell; the prompt has to send the model
    # there rather than let it guess a POSIX command on Windows.
    assert "LOOM_RUNTIME_STATE" in DEFAULT_AGENT_SYSTEM_PROMPT


def test_one_capability_is_offered_under_one_name(tmp_path):
    from app.agent_runtime.tools import ToolExposure

    registry = loom_default_tools()
    offered = {tool.name for tool in registry.router().definitions()}
    deferred = {
        tool.name for tool in registry.all() if tool.exposure is ToolExposure.DEFERRED
    }

    # These duplicate exec / exec_write / write_workspace_text under a second
    # name. Offering both made the model pick between ~50 entries whose only
    # distinguishing text was "Compatibility alias for ...".
    for alias in (
        "run_workspace_command",
        "start_workspace_command",
        "poll_workspace_process",
        "write_workspace_process",
        "interrupt_workspace_process",
        "terminate_workspace_process",
        "write_workspace_note",
    ):
        assert alias not in offered, f"{alias} is back in the model's context"
        assert alias in deferred, f"{alias} must stay reachable through tool_search"

    # The capabilities themselves stay directly available.
    assert {"exec", "exec_write", "exec_wait", "write_workspace_text"} <= offered


def test_a_deferred_alias_is_still_findable_by_its_exact_name():
    registry = loom_default_tools()
    # A durable session created before the unified exec tools can recover the
    # old name in one step rather than failing outright.
    matches = registry.search_deferred("run_workspace_command", limit=3)
    assert matches and matches[0].name == "run_workspace_command"


def test_no_tool_description_is_only_a_compatibility_note():
    registry = loom_default_tools()
    for tool in registry.all():
        assert not tool.description.startswith("Compatibility"), tool.name
        # "Alias for X" says nothing about what the tool does.
        assert len(tool.description) > 45, f"{tool.name}: {tool.description!r}"
