from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from .context_limits import resolve_context_limits
from .contracts import AgentEventKind, AgentSession
from .durable_runtime import DurableAgentRuntime
from .permissions import PermissionDecision, permission_snapshot
from .process_runtime import ProcessStore
from .response_language import infer_user_language
from .sandbox import SandboxManager, SandboxPolicy, SandboxSnapshot
from .sandbox_tools import sandbox_status_tool
from .step import RequestStateSnapshot, StepContext
from .tools import ToolContext, ToolResult


class SandboxAgentRuntime(DurableAgentRuntime):
    """Durable runtime with an explicit OS-sandbox planning boundary.

    The manager never claims isolation that the host cannot enforce. AUTO uses a
    supported backend when available and otherwise records an honest fallback;
    REQUIRED fails closed; OFF deliberately skips OS sandboxing. Full-access
    sessions intentionally remain unsandboxed regardless of runtime policy.
    """

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
        elif supplied_store is not None and supplied_store.sandbox_manager is not sandbox_manager:
            raise ValueError("sandbox_manager must match the supplied process_store")

        if supplied_store is None:
            kwargs["process_store"] = ProcessStore(sandbox_manager=sandbox_manager)

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
        if approved:
            session = self.store.load(session_id)
            pending = session.pending_approval
            requested_call_id = str(call_id or "").strip()
            if pending is not None and pending.call_id == requested_call_id:
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
        return self._execute_prepared_tool(
            session,
            prepared,
            token=token,
            step=step,
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
        result = self.orchestrator.execute(
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
            request_state=request_state,
        )


__all__ = ["SandboxAgentRuntime"]
