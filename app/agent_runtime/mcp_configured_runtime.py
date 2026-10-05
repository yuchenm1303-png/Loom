from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

from . import mcp_runtime as _mcp_runtime
from .browser_backend_registry import BrowserBackendRegistryMixin
from .browser_status_privacy import BrowserStatusPrivacyMixin
from .computer_single_loop_runtime import SingleLoopComputerRuntime
from .computer_windows import windows_computer_available
from .computer_windows_single_loop import SingleLoopWindowsOperator
from .contracts import AgentStatus
from .mcp_runtime import MCPConfigurationError, MCPRuntime, MCPServerConfig, McpBinding
from .step import StepContext
from .tools import ToolRegistry


class ConfiguredMCPRuntime(
    BrowserStatusPrivacyMixin,
    BrowserBackendRegistryMixin,
    SingleLoopComputerRuntime,
    MCPRuntime,
):
    """Default Loom runtime with direct Browser/Computer Use plus exact MCP binding.

    Browser Use exposes explicit current-browser, isolated, and developer-CDP
    backends. The selected backend is discoverable and truthful: current-browser
    requires the Current Tab Bridge and fails fast when it is unavailable rather
    than silently opening a different browser. Browser discovery/status strips
    address-bar paths, queries and fragments before they can become model-visible.
    Computer Use is owned by Loom's existing TurnRunner and the currently selected
    conversation model. The legacy UFO/driver runtime remains importable for
    compatibility tests while the production MRO no longer routes through it.

    MCP authority is captured once per semantic sampling Step. Model-visible MCP
    schemas and executable handlers therefore come from the same immutable
    ``McpBinding``; later manager refresh/reconnect cannot reroute an older Step.
    """

    def __init__(
        self,
        *args: Any,
        mcp_servers: Sequence[MCPServerConfig] | None = None,
        mcp_config_path: str | Path | None = None,
        **kwargs: Any,
    ) -> None:
        resolved_servers = mcp_servers
        self.mcp_config_path = ""
        if resolved_servers is None:
            store = kwargs.get("store")
            if store is None:
                raise ValueError("default MCP config discovery requires the Runtime store")
            root = Path(getattr(store, "root", "")).expanduser().resolve()
            try:
                runtime_home = root.parents[1]
            except IndexError as exc:
                raise ValueError("cannot derive Loom runtime home from the session store") from exc
            selected = Path(
                mcp_config_path
                or os.environ.get("LOOM_CONFIG")
                or (runtime_home / "config.toml")
            ).expanduser().resolve()
            self.mcp_config_path = str(selected)
            resolved_servers = _mcp_runtime.load_mcp_server_configs(selected)
        elif mcp_config_path is not None:
            self.mcp_config_path = str(Path(mcp_config_path).expanduser().resolve())

        # Any Loom-owned browser is visible. BrowserRuntime historically defaults
        # to headless=True because it began life as a backend primitive, but an
        # explicit isolated desktop browser must be inspectable by the user.
        kwargs.setdefault("browser_headless", False)

        # Install the direct visual Windows operator before ComputerUseRuntime has
        # a chance to auto-create the legacy one. Custom embedders can still pass
        # their own operator explicitly, and non-Windows hosts remain disabled.
        auto_computer = bool(kwargs.get("auto_configure_computer", True))
        if kwargs.get("computer_operator") is None and auto_computer and windows_computer_available():
            kwargs["computer_operator"] = SingleLoopWindowsOperator()
            kwargs["auto_configure_computer"] = False

        # SingleLoopComputerRuntime itself owns the invariant that no legacy
        # GUI-Plus/UI-TARS grounder can be instantiated. Keep product assembly
        # concerned only with the concrete OS operator.
        super().__init__(*args, mcp_servers=tuple(resolved_servers or ()), **kwargs)

    @staticmethod
    def _identity_hash(value: object) -> str:
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    def _mcp_binding_snapshot(self, binding: McpBinding) -> dict[str, object]:
        """Secret-free diagnostic projection; never execution authority."""
        return {
            "identity": binding.identity,
            "tools": [
                {
                    "name": descriptor.canonical_name,
                    "server": descriptor.server_name,
                    "remote_name": descriptor.remote_name,
                    "effect": descriptor.effect.value,
                    "exposure": descriptor.exposure.value,
                    "schema_sha256": self._identity_hash(
                        json.dumps(
                            descriptor.input_schema,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    ),
                }
                for descriptor in binding.tools()
            ],
        }

    def _build_step_context(
        self,
        session,
        *,
        next_model_step: bool,
        step_id: str | None = None,
    ) -> StepContext:
        # Capture exact MCP execution authority at the same semantic boundary as
        # the Step. MCPRuntime historically registers an initial compatibility
        # projection in self.tools; filter those stale projection entries and
        # rebuild the per-Step router from the current immutable binding.
        binding = self.mcp_clients.capture_binding()
        bound_tools = binding.agent_tools()
        base_tools = tuple(
            tool
            for tool in self.tools.all()
            if not str(tool.binding_key or "").startswith("mcp-binding:")
        )
        base_names = {tool.name for tool in base_tools}
        collision = next((tool.name for tool in bound_tools if tool.name in base_names), None)
        if collision is not None:
            raise MCPConfigurationError(f"MCP tool conflicts with existing Loom tool: {collision}")
        router = ToolRegistry(tuple((*base_tools, *bound_tools))).router()

        step = super()._build_step_context(
            session,
            next_model_step=next_model_step,
            step_id=step_id,
        )
        binding_json = json.dumps(
            self._mcp_binding_snapshot(binding),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return replace(
            step,
            world_state=replace(
                step.world_state,
                tool_names=tuple(tool.name for tool in router.all()),
            ),
            tool_router=router,
            request_state=replace(
                step.request_state,
                mcp_binding_json=binding_json,
            ),
            mcp_binding=binding,
        )

    def recover_turn_if_idle(self, session_id: str, turn_id: str):
        """Resume a trusted safe handoff using the existing logical turn id.

        This is deliberately narrower than crash recovery: it adds no new user
        message and creates a fresh execution/Step stack. Pending approval or
        pending tool execution is rejected after process restart because the
        original sampled Step and process-local approval authority no longer
        exist; Loom must fail closed rather than reconstruct equivalent-looking
        execution authority from live state.
        """
        resolved_turn_id = str(turn_id or "").strip()
        if not resolved_turn_id:
            raise ValueError("turn_id must not be empty")
        lock = self._session_lock(session_id)
        with lock:
            session = self.get_session(session_id)
            if session.current_turn_id != resolved_turn_id:
                raise ValueError("turn_id does not match the unfinished turn")
            with self._active_tokens_guard:
                if session.session_id in self._active_tokens:
                    raise RuntimeError("turn is still live in this runtime; rejoin it instead")
            if session.status is AgentStatus.WAITING_APPROVAL:
                raise RuntimeError(
                    "pending approval recovery requires the original captured StepContext and "
                    "process-local approval authority"
                )
            if session.status is not AgentStatus.RUNNING:
                raise RuntimeError("thread has no safely suspended unfinished turn")
            if session.pending_tool_calls or session.pending_step_id or session.pending_bindings:
                raise RuntimeError(
                    "safe handoff contains unresolved execution authority; fail closed instead"
                )
            token = self._activate(session.session_id)
            try:
                return self._drive(session, token)
            finally:
                self._deactivate(session.session_id, token)

    def mcp_status(self) -> dict[str, object]:
        status = dict(super().mcp_status())
        status["config_path"] = self.mcp_config_path
        return status


__all__ = ["ConfiguredMCPRuntime"]
