from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Callable, Protocol

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolCall, ToolChoice

from .contracts import (
    AgentEvent,
    AgentEventKind,
    AgentLimits,
    AgentRunResult,
    AgentSession,
    AgentStatus,
    PendingToolApproval,
    PermissionMode,
)
from .diff_tracker import DiffTrackerRegistry
from .model_execution import ModelExecutor
from .instructions import InstructionLoader
from .execution_binding import binding_digest
from .journal import ExecutionLease
from app.ai.execution_control import ModelCancelled
from .orchestrator import PreparedToolCall, ToolOrchestrator
from .permissions import PermissionDecision
from .process_runtime import ProcessStore
from .step import StepContext
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
    "a capability as missing before trying the command."
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


class AgentRuntime:
    """Durable tool-using Agent runtime.

    Each model sampling step receives a frozen ``StepContext``. Every tool call
    crosses ``ToolOrchestrator`` before execution. Runtime-owned services such as
    managed processes and turn diffs are injected into the tool context rather
    than hidden in individual tool modules.
    """

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
        self.store = store
        self.tools = tools or ToolRegistry()
        self.policy = policy or ToolPolicy()
        self.limits = limits or AgentLimits(
            context_window_tokens=int(os.environ.get("LOOM_CONTEXT_WINDOW_TOKENS", "32768")),
            output_reserve_tokens=int(os.environ.get("LOOM_OUTPUT_RESERVE_TOKENS", "4096")),
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

    def close(self) -> None:
        with self._active_tokens_guard:
            tokens = tuple(self._active_tokens.values())
        for token in tokens:
            token.cancel()
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

    def start_turn(self, session_id: str, user_text: TurnInput) -> AgentRunResult:
        content, text = normalize_turn_input(user_text)
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status is AgentStatus.WAITING_APPROVAL:
                raise RuntimeError("agent session is waiting for tool approval")
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
            session.messages.append(AIMessage(role=MessageRole.USER, content=content))
            self._record(
                session,
                AgentEventKind.TURN_STARTED,
                data={"permission_mode": session.permission_mode.value},
            )
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

            validation_step = self._build_step_context(session, next_model_step=False, step_id=session.pending_step_id or None)
            selected_tool = validation_step.tool_router.get(pending.tool_name)
            expected = session.pending_bindings.get(pending.call_id)
            if approved and (selected_tool is None or not expected or binding_digest(validation_step, selected_tool, self.platform) != expected):
                raise ValueError("approval binding changed or is legacy; deny this request and start a new turn")
            session.status = AgentStatus.RUNNING
            session.pending_approval = None
            call = session.pending_tool_calls.pop(0)
            step = self._build_step_context(
                session,
                next_model_step=False,
                step_id=session.pending_step_id or None,
            )
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
                session.error = "Agent process stopped before the active turn reached a durable terminal state."
                self._record(session, AgentEventKind.TURN_INTERRUPTED, data={"error": session.error})
            return self._result(session)

    def _drive(self, session: AgentSession, token: CancellationToken) -> AgentRunResult:
        from .turn_runner import TurnRunner
        return TurnRunner(self).run(session, token)

    def _prepare_model_request(self, session, step, token):
        messages = [AIMessage(role=MessageRole.SYSTEM, content=self._model_system_prompt(session, step))]
        instructions = self.instruction_loader.load(session.workspace_dir)
        if instructions:
            messages.append(AIMessage(role=MessageRole.SYSTEM, name="loom_project_instructions", content=instructions))
        return [*messages, *session.messages], {}

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
            self._record(session, AgentEventKind.USER_MESSAGE, data={"text": item["text"], "source": "steering", "input_id": item["id"]})
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
        execution_step = step or self._build_step_context(
            session,
            next_model_step=False,
            step_id=session.pending_step_id or None,
        )
        while session.pending_tool_calls:
            if self.store.pending_steering(session.session_id, session.current_turn_id):
                while session.pending_tool_calls:
                    abandoned = session.pending_tool_calls.pop(0)
                    self._append_tool_result(session, abandoned, ToolResult(False,
                        "Not executed: new user steering arrived; reconsider this action."), failed=True)
                self._consume_steering(session)
                break
            if self._cancel_if_requested(session, token):
                return False
            call = session.pending_tool_calls[0]
            selected = execution_step.tool_router.get(call.name)
            expected = session.pending_bindings.get(call.call_id)
            if expected and selected is not None and binding_digest(execution_step, selected, self.platform) != expected:
                from .history import repair_tool_history
                session.messages = list(repair_tool_history(session.messages).messages)
                session.pending_tool_calls.clear()
                session.pending_step_id = ""
                session.status = AgentStatus.FAILED
                session.error = "pending tool binding changed; execution stopped"
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
                        "permission_mode": session.permission_mode.value,
                        "step_id": execution_step.step_id,
                    },
                )
                self._append_tool_result(
                    session,
                    call,
                    ToolResult(ok=False, content=f"Tool call blocked by permissions. {prepared.reason}"),
                    failed=True,
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
                        "permission_mode": session.permission_mode.value,
                        "step_id": execution_step.step_id,
                    },
                )
                return False

            session.pending_tool_calls.pop(0)
            if not self._execute_prepared_tool(session, prepared, token=token, step=execution_step):
                return False
        session.pending_step_id = ""
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
        self._record(
            session,
            AgentEventKind.TOOL_STARTED,
            data={"call_id": call.call_id, "tool": call.name, "step_id": step.step_id},
        )
        tracker = self.diff_trackers.for_turn(session.session_id, session.current_turn_id)
        diff_revision_before = tracker.revision
        context = ToolContext(
            session_id=session.session_id,
            turn_id=session.current_turn_id,
            workspace=Path(step.world_state.workspace_dir),
            permission_mode=session.permission_mode.value,
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
        self._append_tool_result(session, call, result, failed=not result.ok)
        if self._cancel_if_requested(session, token):
            return False
        return True

    def _build_step_context(
        self,
        session: AgentSession,
        *,
        next_model_step: bool,
        step_id: str | None = None,
    ) -> StepContext:
        model_step = session.model_steps + (1 if next_model_step else 0)
        return replace(StepContext.build(
            step_id=step_id or str(uuid.uuid4()),
            session_id=session.session_id,
            turn_id=session.current_turn_id,
            model_step=model_step,
            workspace_dir=session.workspace_dir,
            profile_id=session.profile_id,
            permission_mode=session.permission_mode,
            tool_router=self.tools.router(),
        ), environment_policy=self.process_store.environment_policy)

    def _model_system_prompt(self, session: AgentSession, step: StepContext) -> str:
        capability_contract = self.orchestrator.capability_contract(
            step,
            legacy_policy=self.policy,
        )
        return f"{session.system_prompt}\n\n{capability_contract}"

    def _append_tool_result(
        self,
        session: AgentSession,
        call: ToolCall,
        result: ToolResult,
        *,
        failed: bool,
    ) -> None:
        model_payload = result.model_payload(max_chars=self.limits.max_tool_result_chars)
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
