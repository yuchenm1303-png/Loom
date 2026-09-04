from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from .contracts import AgentSession
from .durable_runtime import DurableAgentRuntime
from .process_runtime import ProcessStore
from .sandbox import SandboxManager, SandboxPolicy, SandboxSnapshot
from .sandbox_tools import sandbox_status_tool
from .step import StepContext


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
        return self.process_store.sandbox_snapshot(
            permission_mode=session.permission_mode.value,
            workspace=Path(session.workspace_dir),
        )

    def recover_interrupted(self, session_id: str):
        # Managed processes are intentionally ephemeral. If recovery is invoked
        # in a still-running host, terminate any process launched under the
        # interrupted turn before repairing its canonical history.
        self.process_store.terminate_session(session_id)
        return super().recover_interrupted(session_id)

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
        snapshot = self.process_store.sandbox_snapshot(
            permission_mode=session.permission_mode.value,
            workspace=Path(session.workspace_dir),
        )
        return replace(
            step,
            world_state=replace(step.world_state, sandbox=snapshot),
        )


__all__ = ["SandboxAgentRuntime"]
