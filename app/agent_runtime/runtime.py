from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Callable, Protocol

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolCall, ToolChoice

from .context_limits import resolve_context_limits
from .contracts import (
    AgentEvent,
    AgentEventKind,
    AgentLimits,
    AgentRunResult,
    AgentSession,
    AgentStatus,
    PendingToolApproval,
    PermissionMode,
    ToolEffect,
)
from .diff_tracker import DiffTrackerRegistry
from .model_execution import ModelExecutor
from .instructions import InstructionLoader
from .execution_binding import action_binding_digest
from .journal import ExecutionLease
from app.ai.execution_control import ModelCancelled
from .orchestrator import PreparedToolCall, ToolOrchestrator
from .permissions import PermissionDecision
from .process_runtime import ProcessStore
from .response_language import infer_user_language
from .step import RequestStateSnapshot, StepContext
from .storage import FileAgentSessionStore, utc_now
from .turn_input import TurnInput, normalize_turn_input
from .tools import ToolContext, ToolPolicy, ToolRegistry, ToolResult


# The "answer it by running a command" rule is adapted from the Codex CLI
# system prompt (openai/codex, Apache-2.0). Without it this agent routed
# questions about the host by tool *name*: asked how much RAM was free it
# called memory_status (Loom's own memory store), then told the user to open
# Task Manager. A measured A/B over the real provider showed this paragraph,
# not the runtime-state envelope, is what makes it reach for exec instead.
DEFAULT_AGENT_SYSTEM_PROMPT = (
    "You are an execution agent operating inside a controlled tool harness. "
    "Use only the tools provided to you, never invent tool results, and treat tool errors as observations "
    "you may correct on the next step. Keep private reasoning private; communicate only useful conclusions, "
    "requests for user decisions, and concise action/status summaries.\n"
    "\n"
    "Choose tools by what they do, not by what they are called. Several tool names describe Loom's own "
    "internals rather than the user's computer: memory_status reports Loom's long-term memory store, and "
    "computer_status reports whether Loom's Computer Use feature is configured. Neither one observes the "
    "host machine.\n"
    "\n"
    "If the user asks something about this machine or its environment that a command can answer -- free "
    "memory, disk space, the current time, the OS version, whether a program is installed, what is running "
    "-- run that command with exec and answer from its output. Consult LOOM_RUNTIME_STATE for the platform "
    "and shell before composing it. Do not tell the user to go and look it up themselves, and do not report "
    "a capability as missing before trying the command.\n"
    "\n"
    "Keep the user informed during long work. Before a substantial batch of tool calls, briefly state the "
    "immediate next action; after roughly 8-12 tool calls or a meaningful discovery, give a concise progress "
    "update before continuing. Do not remain silent through a long command stream.\n"
    "\n"
    "Work toward convergence. Before repeating a command, file read, search, or test, check whether its inputs "
    "or relevant workspace state changed. Reuse a durable prior result when they did not. Do not reread files "
    "merely to verify a successful apply_patch. If repeated attempts are not producing new evidence, summarize "
    "what is known and change approach or ask for the missing decision.\n"
    "\n"
    "Do not use the user's project as scratch memory. Put temporary probes, dumps, command captures, backups, "
    "and checkpoint notes in the run scratch directory exposed by the harness. Only create project files that "
    "are requested deliverables or necessary parts of the implementation. Prefer apply_patch for source edits.\n"
    "\n"
    "When you create or save an image inside the active workspace and seeing it would help the user, show it "
    "in the final response with Markdown image syntax using a workspace-relative path with forward slashes, "
    "for example ![preview](artifacts/result.png). If the path contains spaces, wrap the destination in angle "
    "brackets. Do not embed local images as base64 or file:// URLs, and do not leave the user with only a path "
    "when the image itself is the deliverable."
)


class AgentModelPlatform(Protocol):
    def execute_chat(self, profile_id: str, request: ChatRequest) -> ModelResponse:
        ...


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


EventListener = Callable[[AgentEvent], None]


def _optional_env_int(name: str) -> int | None:
    """Read a host-declared limit, or ``None`` when the host declared nothing."""
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


class AgentRuntime:
    """Durable tool-using Agent runtime.

    Each model sampling step receives a frozen ``StepContext``. Every tool call
    crosses ``ToolOrchestrator`` before execution. Runtime-owned services such as
    managed processes and turn diffs are injected into the tool context rather
    than hidden in individual tool modules.
    """

    def _internal_model_stream_scope(self):
        """Hide detached model work from the user-facing response stream."""

        return nullcontext()

    def __init__(
        self,
        *,
        platform: AgentModelPlatform,
        store: FileAgentSessionStore,
        tools: ToolRegistry | None = None,
        policy: ToolPolicy | None = None,
        limits: AgentLimits | None = None,
        default_permission_mode: PermissionMode | str = PermissionMode.APPROVAL,
        orchestrator: ToolOrchestrator | None = None,
        process_store: ProcessStore | None = None,
        diff_trackers: DiffTrackerRegistry | None = None,
    ) -> None:
        self.platform = platform
        self._session_platforms: dict[str, AgentModelPlatform] = {}
        self._session_reasoning: dict[str, object | None] = {}
        self.store = store
        self.tools = tools or ToolRegistry()
        from .evidence_tools import durable_tool_result_tool, run_scratch_dir_tool
        for runtime_tool in (durable_tool_result_tool(store), run_scratch_dir_tool(store)):
            if self.tools.get(runtime_tool.name) is None:
                self.tools.register(runtime_tool)
        self.policy = policy or ToolPolicy()
        # Only the host may declare a window or an output cap. Defaulting these
        # to numbers made Loom budget every unknown model as a 32k one; there is
        # no honest value to invent, so an absent env var declares nothing.
        self.limits = limits or AgentLimits(
            context_window_tokens=_optional_env_int("LOOM_CONTEXT_WINDOW_TOKENS"),
            output_reserve_tokens=_optional_env_int("LOOM_OUTPUT_RESERVE_TOKENS"),
        )
        self.default_permission_mode = PermissionMode(default_permission_mode)
        self.orchestrator = orchestrator or ToolOrchestrator()
        self.process_store = process_store or ProcessStore()
        self.diff_trackers = diff_trackers or DiffTrackerRegistry()
        self.model_executor = ModelExecutor()
        self.instruction_loader = InstructionLoader()
        self._listeners: list[EventListener] = []
        self._session_locks: dict[str, ExecutionLease] = {}
        self._session_locks_guard = threading.Lock()
        self._active_tokens: dict[str, CancellationToken] = {}
        self._active_tokens_guard = threading.RLock()
        # Python adaptation of Codex's Arc<StepContext> ownership. This is
        # intentionally ephemeral: durable recovery owns persistence semantics,
        # while this runtime refuses to reconstruct an execution world from live
        # state after a sampled action has already been admitted.
        self._captured_steps: dict[tuple[str, str, str], StepContext] = {}
        self._captured_steps_guard = threading.RLock()
        # Measuring the fallback estimator's bias costs a full event-log scan.
        # ``prepare_context`` pays for it once per model step; anything earlier in
        # the step that needs to compare estimated tokens against the budget --
        # the tool-schema planner does -- reads the published value instead of
        # scanning the log a second time.
        self._estimator_calibration: dict[str, float] = {}
        self._estimator_calibration_guard = threading.Lock()

    def _publish_estimator_calibration(self, session_id: str, calibration: float) -> None:
        with self._estimator_calibration_guard:
            self._estimator_calibration[str(session_id)] = float(calibration)

    def estimator_calibration(self, session_id: str) -> float:
        """This session's measured estimator bias, or 1.0 before one is observed.

        1.0 means "trust the estimator as-is", which is the honest starting point:
        no model step has completed yet, so there is no provider accounting to
        compare against.
        """
        with self._estimator_calibration_guard:
            return float(self._estimator_calibration.get(str(session_id), 1.0))

    def set_session_model(
        self,
        session_id: str,
        platform: AgentModelPlatform,
        *,
        reasoning=None,
    ) -> None:
        key = str(session_id or "").strip()
        if not key:
            raise ValueError("session_id must not be empty")
        self._session_platforms[key] = platform
        self._session_reasoning[key] = reasoning

    def clear_session_model(self, session_id: str) -> None:
        key = str(session_id or "").strip()
        self._session_platforms.pop(key, None)
        self._session_reasoning.pop(key, None)

    def has_session_model(self, session_id: str) -> bool:
        return str(session_id or "").strip() in self._session_platforms

    def platform_for_session(self, session_id: str) -> AgentModelPlatform:
        key = str(session_id or "").strip()
        return self._session_platforms.get(key, self.platform)

    def reasoning_for_session(self, session_id: str):
        key = str(session_id or "").strip()
        if key in self._session_reasoning:
            return self._session_reasoning[key]
        return getattr(self, "reasoning", None)

    def close(self) -> None:
        self._session_platforms.clear()
        self._session_reasoning.clear()
        with self._active_tokens_guard:
            tokens = tuple(self._active_tokens.values())
        for token in tokens:
            token.cancel()
        with self._captured_steps_guard:
            self._captured_steps.clear()
        self.process_store.terminate_all()

    def subscribe(self, listener: EventListener) -> None:
        if not callable(listener):
            raise TypeError("agent event listener must be callable")
        self._listeners.append(listener)

    def create_session(
        self,
        profile_id: str,
        *,
        system_prompt: str = DEFAULT_AGENT_SYSTEM_PROMPT,
        workspace_dir: str | Path | None = None,
        permission_mode: PermissionMode | str | None = None,
        emit_session_created: bool = True,
    ) -> AgentSession:
        profile = str(profile_id or "").strip().casefold()
        prompt = str(system_prompt or "").strip()
        if not profile or not prompt:
            raise ValueError("agent session requires profile_id and system_prompt")
        session_id = str(uuid.uuid4())
        now = utc_now()
        if workspace_dir is None:
            workspace = (self.store.session_dir(session_id) / "workspace").resolve()
        else:
            workspace = Path(workspace_dir).expanduser().resolve()
            if not workspace.exists():
                raise ValueError(f"agent workspace does not exist: {workspace}")
            if not workspace.is_dir():
                raise ValueError(f"agent workspace is not a directory: {workspace}")
        mode = PermissionMode(permission_mode or self.default_permission_mode)
        session = AgentSession(
            session_id=session_id,
            profile_id=profile,
            system_prompt=prompt,
            workspace_dir=str(workspace),
            created_at=now,
            updated_at=now,
            permission_mode=mode,
        )
        self.store.create(session)
        if emit_session_created:
            self._record(
                session,
                AgentEventKind.SESSION_CREATED,
                data={
                    "profile_id": profile,
                    "workspace_dir": str(workspace),
                    "permission_mode": mode.value,
                },
            )
        return session

    def get_session(self, session_id: str) -> AgentSession:
        return self.store.load(session_id)

    def set_permission_mode(
        self,
        session_id: str,
        mode: PermissionMode | str,
    ) -> AgentSession:
        resolved = PermissionMode(mode)
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                raise RuntimeError("cannot change permissions while a turn is active")
            previous = session.permission_mode
            if previous is resolved:
                return session
            # Loom does not yet have an OS sandbox capable of retroactively
            # constraining an already-running process. Kill stale terminals on
            # any permission transition instead of pretending the new profile
            # was applied to them.
            self.process_store.terminate_session(session.session_id)
            session.permission_mode = resolved
            self._record(
                session,
                AgentEventKind.PERMISSION_CHANGED,
                data={"previous": previous.value, "current": resolved.value},
            )
            return session

    @staticmethod
    def _is_failed_user_input_retry(session: AgentSession, content) -> bool:
        """Return whether a submission reuses the unprocessed tail of a failed turn."""
        return bool(
            session.status is AgentStatus.FAILED
            and session.messages
            and session.messages[-1].role is MessageRole.USER
            and session.messages[-1].content == content
        )

    def start_turn(self, session_id: str, user_text: TurnInput) -> AgentRunResult:
        content, text = normalize_turn_input(user_text)
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status is AgentStatus.WAITING_APPROVAL:
                raise RuntimeError("agent session is waiting for tool approval")
            retrying_failed_input = self._is_failed_user_input_retry(session, content)
            self._release_session_steps(session.session_id)
            turn_id = str(uuid.uuid4())
            session.current_turn_id = turn_id
            session.status = AgentStatus.RUNNING
            session.model_steps = 0
            session.tool_calls = 0
            session.pending_tool_calls.clear()
            session.pending_step_id = ""
            session.pending_approval = None
            session.final_text = ""
            session.error = ""
            self.diff_trackers.for_turn(session.session_id, turn_id)
            if not retrying_failed_input:
                session.messages.append(AIMessage(role=MessageRole.USER, content=content))
            self._record(
                session,
                AgentEventKind.TURN_STARTED,
                data={
                    "permission_mode": session.permission_mode.value,
                    "retrying_failed_input": retrying_failed_input,
                },
            )
            if not retrying_failed_input:
                self._record(session, AgentEventKind.USER_MESSAGE, data={"text": text})
            token = self._activate(session.session_id)
            try:
                return self._drive(session, token)
            finally:
                self._deactivate(session.session_id, token)

    def resume_approval(
        self,
        session_id: str,
        call_id: str,
        *,
        approved: bool,
    ) -> AgentRunResult:
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            pending = session.pending_approval
            if session.status is not AgentStatus.WAITING_APPROVAL or pending is None:
                raise RuntimeError("agent session is not waiting for approval")
            if pending.call_id != str(call_id or "").strip():
                raise ValueError("approval call_id does not match pending tool call")
            if not session.pending_tool_calls or session.pending_tool_calls[0].call_id != pending.call_id:
                raise RuntimeError("pending tool approval state is inconsistent")

            step = self._captured_step_context(
                session,
                step_id=session.pending_step_id or None,
            )
            validation_call = session.pending_tool_calls[0]
            selected_tool = step.tool_router.get(pending.tool_name)
            expected = session.pending_bindings.get(pending.call_id)
            if approved and (
                selected_tool is None
                or not expected
                or action_binding_digest(
                    step,
                    selected_tool,
                    validation_call,
                    self.platform_for_session(session.session_id),
                ) != expected
            ):
                raise ValueError("approval binding changed or is legacy; deny this request and start a new turn")
            session.status = AgentStatus.RUNNING
            session.pending_approval = None
            call = session.pending_tool_calls.pop(0)
            token = self._activate(session.session_id)
            try:
                if approved:
                    self._record(
                        session,
                        AgentEventKind.TOOL_APPROVED,
                        data={"call_id": call.call_id, "tool": call.name, "step_id": step.step_id},
                    )
                    if not self._consume_tool_call(
                        session,
                        call,
                        token=token,
                        step=step,
                        approval_granted=True,
                    ):
                        return self._result(session)
                else:
                    self._record(
                        session,
                        AgentEventKind.TOOL_DENIED,
                        data={
                            "call_id": call.call_id,
                            "tool": call.name,
                            "source": "user",
                            "step_id": step.step_id,
                        },
                    )
                    self._append_tool_result(
                        session,
                        call,
                        ToolResult(ok=False, content="Tool call denied by the user."),
                        failed=True,
                        step=step,
                    )

                if not self._process_pending_tools(session, token, step=step):
                    return self._result(session)
                return self._drive(session, token)
            finally:
                self._deactivate(session.session_id, token)

    def cancel(self, session_id: str) -> AgentRunResult:
        with self._active_tokens_guard:
            token = self._active_tokens.get(session_id)
        if token is not None:
            token.cancel()
            session = self.store.load(session_id)
            return self._result(session)

        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                cancellation = CancellationToken()
                cancellation.cancel()
                self._cancel_if_requested(session, cancellation)
            return self._result(session)

    def recover_interrupted(self, session_id: str) -> AgentRunResult:
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status is AgentStatus.RUNNING:
                session.status = AgentStatus.INTERRUPTED
                session.pending_approval = None
                session.pending_tool_calls.clear()
                session.pending_step_id = ""
                self._release_turn_steps(session)
                session.error = "Agent process stopped before the active turn reached a durable terminal state."
                self._record(session, AgentEventKind.TURN_INTERRUPTED, data={"error": session.error})
            return self._result(session)

    def _drive(self, session: AgentSession, token: CancellationToken) -> AgentRunResult:
        from .turn_runner import TurnRunner
        return TurnRunner(self).run(session, token)

    def _prepare_model_request(self, session, step, token):
        request_state = step.request_state
        captured = request_state.captured
        messages = [AIMessage(role=MessageRole.SYSTEM, content=self._model_system_prompt(session, step))]
        instructions = (
            request_state.project_instructions
            if captured
            else self.instruction_loader.load(session.workspace_dir)
        )
        if instructions:
            messages.append(AIMessage(role=MessageRole.SYSTEM, name="loom_project_instructions", content=instructions))
        extra: dict[str, object] = {}
        if captured and request_state.context_limits is not None:
            extra["context_limits"] = request_state.context_limits.as_dict()
        from .execution_guidance import model_execution_guidance
        guidance, guidance_metadata = model_execution_guidance(
            self.store.events(session.session_id),
            turn_id=session.current_turn_id,
            tool_calls=session.tool_calls,
        )
        if guidance is not None:
            messages.append(guidance)
            extra.update(guidance_metadata)
        return [*messages, *session.messages], extra

    def steer(self, session_id: str, text: str, *, turn_id: str) -> None:
        value = str(text).strip()
        if not value:
            raise ValueError("steering input must not be empty")
        with self._active_tokens_guard:
            token = self._active_tokens.get(session_id)
            session = self.store.load(session_id)
            if token is None or token.cancelled or session.current_turn_id != turn_id:
                raise ValueError("steering target is not the active turn")
            self.store.submit_steering(session_id, turn_id, value)

    def _consume_steering(self, session) -> bool:
        items = self.store.pending_steering(session.session_id, session.current_turn_id)
        consumed = False
        for item in items:
            if item["id"] in session.steering_ids:
                continue
            session.messages.append(AIMessage(role=MessageRole.USER, content=item["text"]))
            session.steering_ids.append(item["id"])
            event_data = {
                "text": item["text"],
                "source": "steering",
                "input_id": item["id"],
            }
            submitted_at = str(item.get("submitted_at") or "").strip()
            if submitted_at:
                event_data["submitted_at"] = submitted_at
            self._record(session, AgentEventKind.USER_MESSAGE, data=event_data)
            consumed = True
        if items:
            self.store.ack_steering(session.session_id, {item["id"] for item in items})
        return consumed

    def _process_pending_tools(
        self,
        session: AgentSession,
        token: CancellationToken,
        *,
        step: StepContext | None = None,
    ) -> bool:
        execution_step = step or self._captured_step_context(
            session,
            step_id=session.pending_step_id or None,
        )
        if execution_step.session_id != session.session_id or execution_step.turn_id != session.current_turn_id:
            raise RuntimeError("captured step does not belong to the active turn")
        if session.pending_step_id and execution_step.step_id != session.pending_step_id:
            raise RuntimeError("pending tool calls do not belong to the supplied step")
        while session.pending_tool_calls:
            if self.store.pending_steering(session.session_id, session.current_turn_id):
                while session.pending_tool_calls:
                    abandoned = session.pending_tool_calls.pop(0)
                    self._append_tool_result(
                        session,
                        abandoned,
                        ToolResult(False, "Not executed: new user steering arrived; reconsider this action."),
                        failed=True,
                        step=execution_step,
                    )
                self._consume_steering(session)
                break
            if self._cancel_if_requested(session, token):
                return False
            call = session.pending_tool_calls[0]
            selected = execution_step.tool_router.get(call.name)
            expected = session.pending_bindings.get(call.call_id)
            if (
                expected
                and selected is not None
                and action_binding_digest(
                    execution_step,
                    selected,
                    call,
                    self.platform_for_session(session.session_id),
                ) != expected
            ):
                from .history import repair_tool_history
                session.messages = list(repair_tool_history(session.messages).messages)
                session.pending_tool_calls.clear()
                session.pending_step_id = ""
                session.status = AgentStatus.FAILED
                session.error = "pending tool binding changed; execution stopped"
                self._release_step_context(execution_step)
                self._record(session, AgentEventKind.TURN_FAILED, data={"error": session.error})
                return False
            try:
                prepared = self.orchestrator.prepare(
                    execution_step,
                    call,
                    legacy_policy=self.policy,
                )
            except ValueError as exc:
                session.pending_tool_calls.pop(0)
                self._append_tool_result(
                    session,
                    call,
                    ToolResult(ok=False, content=f"Invalid tool request: {exc}"),
                    failed=True,
                    step=execution_step,
                )
                continue

            if prepared.decision is PermissionDecision.DENY:
                session.pending_tool_calls.pop(0)
                self._record(
                    session,
                    AgentEventKind.TOOL_DENIED,
                    data={
                        "call_id": call.call_id,
                        "tool": call.name,
                        "source": "permission",
                        "reason": prepared.reason,
                        "permission_mode": execution_step.world_state.permission_mode.value,
                        "step_id": execution_step.step_id,
                    },
                )
                self._append_tool_result(
                    session,
                    call,
                    ToolResult(ok=False, content=f"Tool call blocked by permissions. {prepared.reason}"),
                    failed=True,
                    step=execution_step,
                )
                continue

            if prepared.decision is PermissionDecision.APPROVAL:
                session.pending_approval = PendingToolApproval(
                    call_id=call.call_id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    effect=prepared.tool.effect,
                    reason=prepared.reason,
                )
                session.status = AgentStatus.WAITING_APPROVAL
                self._record(
                    session,
                    AgentEventKind.TOOL_APPROVAL_REQUIRED,
                    data={
                        "call_id": call.call_id,
                        "tool": call.name,
                        "arguments": call.arguments,
                        "effect": prepared.tool.effect.value,
                        "reason": prepared.reason,
                        "permission_mode": execution_step.world_state.permission_mode.value,
                        "step_id": execution_step.step_id,
                    },
                )
                return False

            session.pending_tool_calls.pop(0)
            if not self._execute_prepared_tool(session, prepared, token=token, step=execution_step):
                return False
        session.pending_step_id = ""
        self._release_step_context(execution_step)
        return True

    def _consume_tool_call(
        self,
        session: AgentSession,
        call: ToolCall,
        *,
        token: CancellationToken,
        step: StepContext,
        approval_granted: bool,
    ) -> bool:
        if self._cancel_if_requested(session, token):
            return False
        try:
            prepared = self.orchestrator.prepare(step, call, legacy_policy=self.policy)
        except ValueError as exc:
            self._append_tool_result(
                session,
                call,
                ToolResult(ok=False, content=f"Invalid tool request: {exc}"),
                failed=True,
                step=step,
            )
            return True
        if prepared.decision is PermissionDecision.DENY:
            raise RuntimeError("permission-denied tool reached executor")
        if prepared.decision is PermissionDecision.APPROVAL and not approval_granted:
            raise RuntimeError("approval-required tool reached executor without approval")
        return self._execute_prepared_tool(session, prepared, token=token, step=step)

    def _execute_prepared_tool(
        self,
        session: AgentSession,
        prepared: PreparedToolCall,
        *,
        token: CancellationToken,
        step: StepContext,
    ) -> bool:
        if self._cancel_if_requested(session, token):
            return False
        call = prepared.call
        tool = prepared.tool
        from .execution_guidance import (
            recent_read_only_repeat_count,
            recent_tool_repeat_count,
            tool_call_fingerprint,
        )
        call_fingerprint = tool_call_fingerprint(call)
        if tool.effect is ToolEffect.READ_ONLY:
            repeat_count = recent_read_only_repeat_count(
                self.store.events(session.session_id),
                turn_id=session.current_turn_id,
                fingerprint=call_fingerprint,
            )
        elif tool.effect is ToolEffect.SENSITIVE:
            repeat_count = recent_tool_repeat_count(
                self.store.events(session.session_id),
                turn_id=session.current_turn_id,
                fingerprint=call_fingerprint,
            )
        else:
            repeat_count = 0
        self._record(
            session,
            AgentEventKind.TOOL_STARTED,
            data={
                "call_id": call.call_id,
                "tool": call.name,
                "step_id": step.step_id,
                "effect": tool.effect.value,
                "call_fingerprint": call_fingerprint,
                "repeat_count": repeat_count,
            },
        )
        tracker = self.diff_trackers.for_turn(session.session_id, session.current_turn_id)
        diff_revision_before = tracker.revision
        context = ToolContext(
            session_id=session.session_id,
            turn_id=session.current_turn_id,
            workspace=Path(step.world_state.workspace_dir),
            permission_mode=step.world_state.permission_mode.value,
            is_cancelled=lambda: token.cancelled,
            services={
                "process_store": self.process_store,
                "permission_snapshot": step.permissions,
                "environment_policy": step.environment_policy,
                "active_skills": session.active_skills,
                "diff_tracker": tracker,
            },
            emit_event=lambda kind, data: self._record(session, kind, data=data),
        )
        try:
            result = tool.handler(context, call.arguments)
            if not isinstance(result, ToolResult):
                raise TypeError("agent tool handler must return ToolResult")
        except Exception as exc:
            result = ToolResult(ok=False, content=f"{type(exc).__name__}: {exc}")

        if tracker.revision != diff_revision_before:
            snapshot = tracker.snapshot(max_chars=self.limits.max_tool_result_chars)
            self._record(
                session,
                AgentEventKind.TURN_DIFF_UPDATED,
                data={
                    "revision": snapshot.revision,
                    "paths": list(snapshot.paths),
                    "diff": snapshot.diff,
                    "truncated": snapshot.truncated,
                },
            )
        self._append_tool_result(session, call, result, failed=not result.ok, step=step)
        if self._cancel_if_requested(session, token):
            return False
        return True

    def _model_profile_snapshot(
        self,
        profile_id: str,
        platform: AgentModelPlatform | None = None,
    ) -> dict[str, object] | None:
        registry = getattr(platform or self.platform, "registry", None)
        if registry is None:
            return None
        try:
            profile = registry.get(profile_id)
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        safe = getattr(profile, "as_safe_dict", None)
        if not callable(safe):
            return None
        payload = safe()
        return dict(payload) if isinstance(payload, dict) else None

    def _build_step_context(
        self,
        session: AgentSession,
        *,
        next_model_step: bool,
        step_id: str | None = None,
    ) -> StepContext:
        model_step = session.model_steps + (1 if next_model_step else 0)
        platform = self.platform_for_session(session.session_id)
        request_state = RequestStateSnapshot.build(
            system_prompt=session.system_prompt,
            project_instructions=self.instruction_loader.load(session.workspace_dir),
            communication_language=infer_user_language(
                session.messages,
                fallback=session.communication_language,
            ),
            model_profile=self._model_profile_snapshot(session.profile_id, platform),
            context_limits=resolve_context_limits(self, session),
        )
        return replace(StepContext.build(
            step_id=step_id or str(uuid.uuid4()),
            session_id=session.session_id,
            turn_id=session.current_turn_id,
            model_step=model_step,
            workspace_dir=session.workspace_dir,
            profile_id=session.profile_id,
            permission_mode=session.permission_mode,
            tool_router=self.tools.router(),
            request_state=request_state,
            reasoning=self.reasoning_for_session(session.session_id),
        ), environment_policy=self.process_store.environment_policy)

    def _capture_step_context(
        self,
        session: AgentSession,
        *,
        next_model_step: bool,
        step_id: str | None = None,
    ) -> StepContext:
        """Capture one complete request world after the runtime MRO has resolved it."""

        step = self._build_step_context(
            session,
            next_model_step=next_model_step,
            step_id=step_id,
        )
        key = (step.session_id, step.turn_id, step.step_id)
        with self._captured_steps_guard:
            existing = self._captured_steps.get(key)
            if existing is not None and existing is not step:
                raise RuntimeError("step id was already captured with a different execution world")
            self._captured_steps[key] = step
        return step

    def _captured_step_context(
        self,
        session: AgentSession,
        *,
        step_id: str | None = None,
    ) -> StepContext:
        resolved_step_id = str(step_id or session.pending_step_id or "").strip()
        if not resolved_step_id:
            raise RuntimeError("pending tool execution has no captured step id")
        key = (session.session_id, session.current_turn_id, resolved_step_id)
        with self._captured_steps_guard:
            step = self._captured_steps.get(key)
        if step is None:
            raise RuntimeError(
                "captured step context is unavailable; pending action cannot be safely resumed"
            )
        return step

    def _release_step_context(self, step: StepContext) -> None:
        key = (step.session_id, step.turn_id, step.step_id)
        with self._captured_steps_guard:
            if self._captured_steps.get(key) is step:
                self._captured_steps.pop(key, None)

    def _release_turn_steps(self, session: AgentSession) -> None:
        prefix = (session.session_id, session.current_turn_id)
        with self._captured_steps_guard:
            stale = [key for key in self._captured_steps if key[:2] == prefix]
            for key in stale:
                self._captured_steps.pop(key, None)

    def _release_session_steps(self, session_id: str) -> None:
        resolved = str(session_id or "").strip()
        with self._captured_steps_guard:
            stale = [key for key in self._captured_steps if key[0] == resolved]
            for key in stale:
                self._captured_steps.pop(key, None)

    def _model_system_prompt(self, session: AgentSession, step: StepContext) -> str:
        capability_contract = self.orchestrator.capability_contract(
            step,
            legacy_policy=self.policy,
        )
        base_prompt = (
            step.request_state.system_prompt
            if step.request_state.captured
            else session.system_prompt
        )
        return f"{base_prompt}\n\n{capability_contract}"

    def _append_tool_result(
        self,
        session: AgentSession,
        call: ToolCall,
        result: ToolResult,
        *,
        failed: bool,
        step: StepContext | None = None,
    ) -> None:
        # Use the same immutable model limits that governed the tool call whenever
        # possible. The durable event below keeps the exact result; only active
        # model history gets the bounded projection.
        context_limits = (
            step.request_state.context_limits
            if step is not None and step.request_state.context_limits is not None
            else resolve_context_limits(self, session)
        )
        model_payload = result.model_payload(
            max_tokens=context_limits.tool_output_token_limit,
        )
        session.messages.append(
            AIMessage(
                role=MessageRole.TOOL,
                content=model_payload,
                name=call.name,
                tool_call_id=call.call_id,
            )
        )
        self._record(
            session,
            AgentEventKind.TOOL_FAILED if failed else AgentEventKind.TOOL_COMPLETED,
            data={
                "call_id": call.call_id,
                "tool": call.name,
                "ok": result.ok,
                "content": result.content,
                "data": result.data,
            },
        )

    def _limit(self, session: AgentSession, reason: str) -> AgentRunResult:
        session.status = AgentStatus.LIMIT_REACHED
        session.error = reason
        session.pending_approval = None
        session.pending_tool_calls.clear()
        session.pending_step_id = ""
        self._release_turn_steps(session)
        self._record(session, AgentEventKind.LIMIT_REACHED, data={"reason": reason})
        return self._result(session)

    def _cancel_if_requested(self, session: AgentSession, token: CancellationToken) -> bool:
        if not token.cancelled:
            return False
        if session.status is AgentStatus.CANCELLED:
            return True
        session.status = AgentStatus.CANCELLED
        session.pending_approval = None
        session.pending_tool_calls.clear()
        session.pending_step_id = ""
        self._release_turn_steps(session)
        session.error = "cancelled by user"
        from .history import repair_tool_history
        session.messages = list(repair_tool_history(session.messages, max_tool_result_chars=self.limits.max_tool_result_chars).messages)
        self._record(session, AgentEventKind.TURN_CANCELLED, data={})
        return True

    def _record(
        self,
        session: AgentSession,
        kind: AgentEventKind,
        *,
        data: dict[str, object],
    ) -> AgentEvent:
        json.dumps(data, ensure_ascii=False)
        event = AgentEvent(
            event_id=str(uuid.uuid4()),
            session_id=session.session_id,
            turn_id=session.current_turn_id,
            kind=kind,
            created_at=utc_now(),
            data=dict(data),
        )
        self.store.commit_event(session, event)
        for listener in tuple(self._listeners):
            try:
                listener(event)
            except Exception:
                continue
        return event

    def _result(self, session: AgentSession) -> AgentRunResult:
        return AgentRunResult(
            session_id=session.session_id,
            turn_id=session.current_turn_id,
            status=session.status,
            final_text=session.final_text,
            pending_approval=session.pending_approval,
            usage=session.usage,
            error=session.error,
        )

    def _session_lock(self, session_id: str) -> ExecutionLease:
        key = str(session_id or "").strip()
        with self._session_locks_guard:
            if key not in self._session_locks:
                self._session_locks[key] = ExecutionLease(self.store.session_dir(key))
            return self._session_locks[key]

    def _activate(self, session_id: str) -> CancellationToken:
        token = CancellationToken()
        with self._active_tokens_guard:
            if session_id in self._active_tokens:
                raise RuntimeError("agent session already has an active turn")
            self._active_tokens[session_id] = token
        return token

    def _deactivate(self, session_id: str, token: CancellationToken) -> None:
        with self._active_tokens_guard:
            if self._active_tokens.get(session_id) is token:
                self._active_tokens.pop(session_id, None)


def _add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


__all__ = [
    "AgentModelPlatform",
    "AgentRuntime",
    "CancellationToken",
    "DEFAULT_AGENT_SYSTEM_PROMPT",
]
