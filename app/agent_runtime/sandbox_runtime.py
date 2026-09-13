from __future__ import annotations

import os
from copy import deepcopy
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path

from .context_limits import resolve_context_limits
from .contracts import (
    AgentEventKind,
    AgentSession,
    AgentStatus,
    ApprovalKind,
    PendingToolApproval,
)
from .durable_runtime import DurableAgentRuntime
from .orchestrator import SandboxRetryDisposition
from .permissions import PermissionDecision, SandboxPermissions, permission_snapshot
from .process_runtime import ProcessStore
from .response_language import infer_user_language
from .sandbox import SandboxManager, SandboxMode, SandboxPolicy, SandboxSnapshot
from .sandbox_attempt import (
    AttemptAwareSandboxManager,
    SandboxAttempt,
    SandboxAttemptKind,
    current_sandbox_attempt,
    ensure_attempt_aware_sandbox_manager,
    sandbox_attempt_scope,
)
from .sandbox_tools import sandbox_status_tool
from .step import RequestStateSnapshot, StepContext
from .tools import ToolContext, ToolResult, ToolRouter


_APPROVAL_ATTEMPT: ContextVar[tuple[str, str, SandboxAttempt] | None] = ContextVar(
    "loom_approval_sandbox_attempt",
    default=None,
)


def _sandbox_manager_identity(manager):
    if isinstance(manager, AttemptAwareSandboxManager):
        return manager.base
    return manager


def _exec_router_with_permission_schema(router: ToolRouter) -> ToolRouter:
    """Expose Codex sandbox permission fields without changing non-exec aliases."""

    changed = False
    tools = []
    for tool in router.all():
        if tool.name != "exec":
            tools.append(tool)
            continue
        schema = deepcopy(tool.input_schema)
        properties = schema.setdefault("properties", {})
        properties.update(
            {
                "sandbox_permissions": {
                    "type": "string",
                    "enum": [value.value for value in SandboxPermissions],
                    "description": (
                        "Use the default sandbox, request full escalation, or request scoped "
                        "additional permissions while remaining sandboxed."
                    ),
                },
                "additional_permissions": {
                    "type": "object",
                    "properties": {
                        "network": {
                            "type": "object",
                            "properties": {"enabled": {"type": "boolean"}},
                            "additionalProperties": False,
                        },
                        "file_system": {
                            "type": "object",
                            "properties": {
                                "read": {"type": "array", "items": {"type": "string"}},
                                "write": {"type": "array", "items": {"type": "string"}},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": False,
                },
                "justification": {"type": "string"},
                "prefix_rule": {"type": "array", "items": {"type": "string"}},
            }
        )
        tools.append(replace(tool, input_schema=schema))
        changed = True
    return ToolRouter(tuple(tools)) if changed else router


class SandboxAgentRuntime(DurableAgentRuntime):
    """Durable runtime with Codex-style approval/sandbox attempt orchestration."""

    def __init__(
        self,
        *args,
        sandbox_manager: SandboxManager | None = None,
        sandbox_policy: SandboxPolicy | str | None = None,
        **kwargs,
    ) -> None:
        supplied_store = kwargs.get("process_store")
        if supplied_store is not None and not isinstance(supplied_store, ProcessStore):
            raise TypeError("process_store must be ProcessStore")

        if sandbox_manager is None:
            if supplied_store is not None:
                sandbox_manager = supplied_store.sandbox_manager
            else:
                resolved_policy = sandbox_policy
                if resolved_policy is None:
                    resolved_policy = str(os.environ.get("LOOM_SANDBOX_POLICY") or SandboxPolicy.AUTO.value)
                sandbox_manager = SandboxManager(policy=resolved_policy)
        elif supplied_store is not None and (
            _sandbox_manager_identity(supplied_store.sandbox_manager)
            is not _sandbox_manager_identity(sandbox_manager)
        ):
            raise ValueError("sandbox_manager must match the supplied process_store")

        sandbox_manager = ensure_attempt_aware_sandbox_manager(sandbox_manager)
        if supplied_store is None:
            kwargs["process_store"] = ProcessStore(sandbox_manager=sandbox_manager)
        else:
            supplied_store.sandbox_manager = sandbox_manager

        super().__init__(*args, **kwargs)
        self.sandbox_manager = self.process_store.sandbox_manager
        tool = sandbox_status_tool()
        if self.tools.get(tool.name) is None:
            self.tools.register(tool)

    def sandbox_status(self, session_id: str) -> SandboxSnapshot:
        session = self.store.load(session_id)
        permissions = permission_snapshot(session.permission_mode)
        return self.sandbox_manager.snapshot(
            permissions=permissions,
            workspace=Path(session.workspace_dir),
        )

    def recover_interrupted(self, session_id: str):
        self.process_store.terminate_session(session_id)
        return super().recover_interrupted(session_id)

    def resume_approval(
        self,
        session_id: str,
        call_id: str,
        *,
        approved: bool,
    ):
        session = self.store.load(session_id)
        pending = session.pending_approval
        requested_call_id = str(call_id or "").strip()
        if approved and pending is not None and pending.call_id == requested_call_id:
            if not session.pending_tool_calls:
                raise RuntimeError("pending tool approval state is inconsistent")
            call = session.pending_tool_calls[0]
            if (
                call.call_id != pending.call_id
                or call.name != pending.tool_name
                or dict(call.arguments) != dict(pending.arguments)
            ):
                raise ValueError(
                    "approved tool action changed while waiting; deny this request and start a new turn"
                )

        if (
            approved
            and pending is not None
            and pending.call_id == requested_call_id
            and pending.kind is ApprovalKind.SANDBOX_ESCALATION
        ):
            attempt = SandboxAttempt.retry_without_sandbox(
                pending.retry_reason or pending.reason
            )
            token = _APPROVAL_ATTEMPT.set((session_id, requested_call_id, attempt))
            try:
                return super().resume_approval(
                    session_id,
                    call_id,
                    approved=True,
                )
            finally:
                _APPROVAL_ATTEMPT.reset(token)

        return super().resume_approval(
            session_id,
            call_id,
            approved=approved,
        )

    def _consume_tool_call(
        self,
        session,
        call,
        *,
        token,
        step,
        approval_granted: bool,
    ) -> bool:
        planned = _APPROVAL_ATTEMPT.get()
        if (
            planned is not None
            and planned[0] == session.session_id
            and planned[1] == call.call_id
        ):
            with sandbox_attempt_scope(planned[2]):
                return self._consume_tool_call_once(
                    session,
                    call,
                    token=token,
                    step=step,
                    approval_granted=approval_granted,
                )
        return self._consume_tool_call_once(
            session,
            call,
            token=token,
            step=step,
            approval_granted=approval_granted,
        )

    def _consume_tool_call_once(
        self,
        session,
        call,
        *,
        token,
        step,
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

        active = current_sandbox_attempt()
        if prepared.tool.name == "exec" and active.kind is SandboxAttemptKind.INITIAL:
            attempt = SandboxAttempt.initial(
                prepared.sandbox_permissions,
                prepared.additional_permissions,
            )
            with sandbox_attempt_scope(attempt):
                return self._execute_prepared_tool(
                    session,
                    prepared,
                    token=token,
                    step=step,
                    approval_granted=approval_granted,
                )
        return self._execute_prepared_tool(
            session,
            prepared,
            token=token,
            step=step,
            approval_granted=approval_granted,
        )

    def _request_sandbox_retry_approval(self, session, prepared, step, result: ToolResult) -> None:
        call = prepared.call
        retry_reason = str(result.content or "sandbox containment rejected the first attempt").strip()
        reason = (
            "The sandbox denied this approved exec attempt. Current policy requires a fresh review "
            "before the same bound action may retry once without OS-level containment."
        )
        session.pending_tool_calls.insert(0, call)
        session.pending_approval = PendingToolApproval(
            call_id=call.call_id,
            tool_name=call.name,
            arguments=dict(call.arguments),
            effect=prepared.tool.effect,
            reason=reason,
            kind=ApprovalKind.SANDBOX_ESCALATION,
            retry_reason=retry_reason,
        )
        session.status = AgentStatus.WAITING_APPROVAL
        attempt = current_sandbox_attempt()
        self._record(
            session,
            AgentEventKind.TOOL_FAILED,
            data={
                "call_id": call.call_id,
                "tool": call.name,
                "ok": False,
                "content": result.content,
                "data": result.data,
                "step_id": step.step_id,
                "sandbox_attempt": attempt.to_dict(),
                "retry_pending": True,
            },
        )
        self._record(
            session,
            AgentEventKind.TOOL_APPROVAL_REQUIRED,
            data={
                "call_id": call.call_id,
                "tool": call.name,
                "arguments": call.arguments,
                "effect": prepared.tool.effect.value,
                "reason": reason,
                "retry_reason": retry_reason,
                "kind": ApprovalKind.SANDBOX_ESCALATION.value,
                "permission_mode": session.permission_mode.value,
                "step_id": step.step_id,
            },
        )

    def _run_prepared_once(self, prepared, context, *, approval_granted: bool) -> ToolResult:
        return self.orchestrator.execute(
            prepared,
            context,
            approval_granted=approval_granted,
        )

    def _execute_prepared_tool(
        self,
        session,
        prepared,
        *,
        token,
        step,
        approval_granted: bool = False,
    ) -> bool:
        if self._cancel_if_requested(session, token):
            return False
        call = prepared.call
        attempt = current_sandbox_attempt()
        self._record(
            session,
            AgentEventKind.TOOL_STARTED,
            data={
                "call_id": call.call_id,
                "tool": call.name,
                "step_id": step.step_id,
                "sandbox_attempt": attempt.to_dict(),
            },
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
        result = self._run_prepared_once(
            prepared,
            context,
            approval_granted=approval_granted,
        )

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

        # Never recursively plan from the retry attempt. Codex performs at most
        # one retry after the typed first-attempt sandbox denial.
        if prepared.tool.name == "exec" and attempt.kind is SandboxAttemptKind.INITIAL:
            plan = self.orchestrator.sandbox_retry_plan(
                step,
                prepared,
                result,
                already_approved=approval_granted,
            )
            if plan.disposition is SandboxRetryDisposition.WITHOUT_SANDBOX:
                if plan.approval_required:
                    self._request_sandbox_retry_approval(session, prepared, step, result)
                    return False
                retry_attempt = SandboxAttempt.retry_without_sandbox(plan.retry_reason)
                with sandbox_attempt_scope(retry_attempt):
                    self._record(
                        session,
                        AgentEventKind.TOOL_STARTED,
                        data={
                            "call_id": call.call_id,
                            "tool": call.name,
                            "step_id": step.step_id,
                            "sandbox_attempt": retry_attempt.to_dict(),
                            "retry": True,
                        },
                    )
                    result = self._run_prepared_once(
                        prepared,
                        context,
                        approval_granted=approval_granted,
                    )

        self._append_tool_result(session, call, result, failed=not result.ok)
        if self._cancel_if_requested(session, token):
            return False
        return True

    def _model_profile_snapshot(self, profile_id: str) -> dict[str, object] | None:
        registry = getattr(self.platform, "registry", None)
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
        step = super()._build_step_context(
            session,
            next_model_step=next_model_step,
            step_id=step_id,
        )
        snapshot = self.sandbox_manager.snapshot(
            permissions=step.permissions,
            workspace=Path(step.world_state.workspace_dir),
        )
        request_state = RequestStateSnapshot.build(
            system_prompt=session.system_prompt,
            project_instructions=self.instruction_loader.load(session.workspace_dir),
            communication_language=infer_user_language(
                session.messages,
                fallback=session.communication_language,
            ),
            model_profile=self._model_profile_snapshot(session.profile_id),
            context_limits=resolve_context_limits(self, session),
        )
        return replace(
            step,
            world_state=replace(step.world_state, sandbox=snapshot),
            tool_router=_exec_router_with_permission_schema(step.tool_router),
            request_state=request_state,
        )


__all__ = ["SandboxAgentRuntime"]
