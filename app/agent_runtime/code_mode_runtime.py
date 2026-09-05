from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.ai import ToolCall

from .code_mode import CodeModeError, CodeModeInterpreter, CodeModeLimits
from .contracts import AgentEventKind, AgentSession, ToolEffect
from .memory_store import redact_secrets
from .permissions import PermissionDecision
from .skills_runtime import SkillRuntime
from .step import StepContext
from .tools import AgentTool, ToolContext, ToolExposure, ToolResult, ToolRouter


_CODE_MODE_TOOL_NAME = "code_mode"


class CodeModeRuntime(SkillRuntime):
    """Top-level runtime layer for bounded nested tool composition.

    Code Mode source is interpreted by ``CodeModeInterpreter`` rather than
    Python ``eval``/``exec``. Nested calls are re-routed through the same
    ``ToolOrchestrator`` and permission profile used by normal model tool calls.
    Interactive approvals intentionally do not happen inside a code cell in v1:
    a nested call that requires approval is returned to the model as blocked so
    it can issue that tool normally and enter Loom's durable approval flow.
    """

    def __init__(
        self,
        *args: Any,
        code_mode_limits: CodeModeLimits | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.code_mode_limits = code_mode_limits or CodeModeLimits()
        tool = self._code_mode_tool()
        if self.tools.get(tool.name) is not None:
            raise ValueError(f"code mode conflicts with existing tool: {tool.name}")
        self.tools.register(tool)

    def _code_mode_tool(self) -> AgentTool:
        return AgentTool(
            name=_CODE_MODE_TOOL_NAME,
            description=self._code_mode_description(),
            input_schema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Restricted Python-like source that composes Loom tools. "
                            "Use emit(value) for the model-visible result."
                        ),
                    }
                },
                "required": ["code"],
                "additionalProperties": False,
            },
            handler=self._code_mode_handler_guard,
            effect=ToolEffect.READ_ONLY,
            exposure=ToolExposure.DIRECT,
        )

    def _code_mode_description(self) -> str:
        catalog: list[str] = []
        for tool in self.tools.all():
            if tool.exposure not in {ToolExposure.DIRECT, ToolExposure.CODE_MODE_ONLY}:
                continue
            if tool.name == _CODE_MODE_TOOL_NAME:
                continue
            properties = tool.input_schema.get("properties") or {}
            required = set(tool.input_schema.get("required") or [])
            params: list[str] = []
            for name in properties:
                params.append(name if name in required else f"{name}?")
            if tool.name.replace("_", "a").isalnum() and "." not in tool.name and "-" not in tool.name:
                call = f"tools.{tool.name}({', '.join(params)})"
            else:
                call = f'tool("{tool.name}", {{...}})'
            catalog.append(f"- {call}: {tool.description[:180]}")
        catalog_text = "\n".join(catalog)
        if len(catalog_text) > 10_000:
            catalog_text = catalog_text[:9_980] + "\n…[catalog truncated]"
        return (
            "Compose several Loom tool calls inside one bounded code cell. This is not arbitrary Python: "
            "imports, functions/classes, while/try/with, host filesystem APIs, subprocess APIs, arbitrary attributes, "
            "eval and exec are unavailable. Supported constructs include assignments, if, bounded for loops, "
            "break/continue, JSON literals, indexing/slicing, comparisons, arithmetic, and safe helpers such as "
            "len/range/get/keys/values/items/sorted/min/max/sum/json. Call tools with tools.<name>(keyword=value) "
            "or tool(\"full.tool.name\", {\"arg\": value}). Call emit(value) to return useful output. Every nested "
            "tool invocation still passes through Loom's ToolOrchestrator and PermissionEngine. If a nested tool requires "
            "interactive approval, Code Mode will NOT execute it; call that tool normally outside Code Mode so the user "
            "can approve it. Deferred tools must first be activated with tool_search. Never place secrets in code source.\n\n"
            "Direct nested tool catalog:\n"
            f"{catalog_text or '- No direct nested tools are currently registered.'}"
        )

    @staticmethod
    def _code_mode_handler_guard(_context: ToolContext, _arguments: dict[str, Any]) -> ToolResult:
        raise RuntimeError("code_mode must be executed through CodeModeRuntime")

    def _execute_prepared_tool(
        self,
        session: AgentSession,
        prepared,
        *,
        token,
        step: StepContext,
    ) -> bool:
        if prepared.tool.name != _CODE_MODE_TOOL_NAME:
            return super()._execute_prepared_tool(session, prepared, token=token, step=step)
        if self._cancel_if_requested(session, token):
            return False

        call = prepared.call
        self._record(
            session,
            AgentEventKind.TOOL_STARTED,
            data={"call_id": call.call_id, "tool": call.name, "step_id": step.step_id},
        )
        try:
            interpreter = CodeModeInterpreter(limits=self.code_mode_limits)
            execution = interpreter.execute(
                str(call.arguments.get("code") or ""),
                invoke_tool=lambda tool_name, arguments: self._invoke_code_mode_tool(
                    session,
                    parent_call_id=call.call_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    token=token,
                    step=step,
                ),
                is_cancelled=lambda: token.cancelled,
            )
            output = execution.output.strip()
            if not output:
                output = self._fallback_summary(execution.tool_summaries)
            result = ToolResult(
                ok=True,
                content=output or "Code Mode completed without emitted output.",
                data={
                    "statements": execution.statements,
                    "loop_iterations": execution.loop_iterations,
                    "nested_tool_calls": execution.nested_tool_calls,
                    "tools": list(execution.tool_summaries),
                },
            )
        except Exception as exc:
            message = str(exc) if isinstance(exc, (CodeModeError, RuntimeError)) else f"{type(exc).__name__}: {exc}"
            result = ToolResult(ok=False, content=redact_secrets(message))

        self._append_tool_result(session, call, result, failed=not result.ok)
        if self._cancel_if_requested(session, token):
            return False
        return True

    @staticmethod
    def _fallback_summary(summaries: tuple[dict[str, Any], ...]) -> str:
        if not summaries:
            return "Code Mode completed without nested tool calls or emitted output."
        parts: list[str] = []
        for item in summaries:
            status = "ok" if item.get("ok") else "failed"
            if item.get("requires_approval"):
                status = "approval required"
            detail = str(item.get("content") or "").strip().replace("\n", " ")[:400]
            parts.append(f"{item.get('tool', 'tool')}: {status}" + (f" — {detail}" if detail else ""))
        return "\n".join(parts)

    def _code_mode_router(self, session: AgentSession) -> ToolRouter:
        activated = set(self._activation_names(session))
        visible: list[AgentTool] = []
        for tool in self.tools.all():
            if tool.name == _CODE_MODE_TOOL_NAME:
                continue
            if tool.exposure in {ToolExposure.DIRECT, ToolExposure.CODE_MODE_ONLY}:
                visible.append(tool)
            elif tool.exposure is ToolExposure.DEFERRED and tool.name in activated:
                visible.append(tool)
        return ToolRouter(tuple(visible))

    def _invoke_code_mode_tool(
        self,
        session: AgentSession,
        *,
        parent_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        token,
        step: StepContext,
    ) -> dict[str, Any]:
        if self._cancel_if_requested(session, token):
            raise RuntimeError("code_mode execution cancelled")
        session.tool_calls += 1
        if session.tool_calls > self.limits.max_tool_calls:
            return {
                "ok": False,
                "content": "Nested tool call blocked because the turn tool-call limit was reached.",
                "data": {"limit_reached": True},
            }

        nested_call = ToolCall(
            call_id=f"{parent_call_id}:nested:{uuid.uuid4().hex[:12]}",
            name=str(tool_name or "").strip(),
            arguments=dict(arguments),
        )
        router = self._code_mode_router(session)
        nested_step = replace(
            step,
            tool_router=router,
            world_state=replace(
                step.world_state,
                tool_names=tuple(tool.name for tool in router.all()),
            ),
        )
        self._record(
            session,
            AgentEventKind.TOOL_REQUESTED,
            data={
                "call_id": nested_call.call_id,
                "tool": nested_call.name,
                "arguments": nested_call.arguments,
                "step_id": step.step_id,
                "nested": True,
                "parent_call_id": parent_call_id,
            },
        )
        try:
            prepared = self.orchestrator.prepare(
                nested_step,
                nested_call,
                legacy_policy=self.policy,
            )
        except ValueError as exc:
            payload = {
                "ok": False,
                "content": f"Invalid or unavailable nested tool request: {exc}",
                "data": {},
            }
            self._record_nested_result(session, nested_call, payload, failed=True, parent_call_id=parent_call_id)
            return payload

        if prepared.decision is PermissionDecision.DENY:
            self._record(
                session,
                AgentEventKind.TOOL_DENIED,
                data={
                    "call_id": nested_call.call_id,
                    "tool": nested_call.name,
                    "source": "permission",
                    "reason": prepared.reason,
                    "permission_mode": session.permission_mode.value,
                    "step_id": step.step_id,
                    "nested": True,
                    "parent_call_id": parent_call_id,
                },
            )
            return {
                "ok": False,
                "content": f"Nested tool call blocked by permissions. {prepared.reason}",
                "data": {"denied": True, "effect": prepared.tool.effect.value},
            }

        if prepared.decision is PermissionDecision.APPROVAL:
            self._record(
                session,
                AgentEventKind.TOOL_DENIED,
                data={
                    "call_id": nested_call.call_id,
                    "tool": nested_call.name,
                    "source": "code_mode_requires_approval",
                    "reason": prepared.reason,
                    "permission_mode": session.permission_mode.value,
                    "step_id": step.step_id,
                    "nested": True,
                    "parent_call_id": parent_call_id,
                },
            )
            return {
                "ok": False,
                "requires_approval": True,
                "content": (
                    f"Nested tool {nested_call.name!r} requires interactive approval and was not executed. "
                    "Call it normally outside code_mode to enter Loom's approval flow."
                ),
                "data": {
                    "effect": prepared.tool.effect.value,
                    "reason": prepared.reason,
                },
            }

        self._record(
            session,
            AgentEventKind.TOOL_STARTED,
            data={
                "call_id": nested_call.call_id,
                "tool": nested_call.name,
                "step_id": step.step_id,
                "nested": True,
                "parent_call_id": parent_call_id,
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
                "diff_tracker": tracker,
            },
            emit_event=lambda kind, data: self._record(session, kind, data=data),
        )
        try:
            nested_result = prepared.tool.handler(context, nested_call.arguments)
            if not isinstance(nested_result, ToolResult):
                raise TypeError("agent tool handler must return ToolResult")
        except Exception as exc:
            nested_result = ToolResult(ok=False, content=f"{type(exc).__name__}: {exc}")

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
                    "nested": True,
                    "parent_call_id": parent_call_id,
                },
            )

        payload = self._safe_tool_result(nested_result)
        self._record_nested_result(
            session,
            nested_call,
            payload,
            failed=not nested_result.ok,
            parent_call_id=parent_call_id,
        )
        return payload

    def _safe_tool_result(self, result: ToolResult) -> dict[str, Any]:
        serialized = result.model_payload(max_chars=self.limits.max_tool_result_chars)
        try:
            payload = json.loads(serialized)
        except json.JSONDecodeError:
            payload = {"ok": bool(result.ok), "content": "Nested tool result could not be serialized.", "data": {}}
        return self._redact_value(payload)

    def _record_nested_result(
        self,
        session: AgentSession,
        call: ToolCall,
        payload: dict[str, Any],
        *,
        failed: bool,
        parent_call_id: str,
    ) -> None:
        self._record(
            session,
            AgentEventKind.TOOL_FAILED if failed else AgentEventKind.TOOL_COMPLETED,
            data={
                "call_id": call.call_id,
                "tool": call.name,
                "ok": bool(payload.get("ok")),
                "content": str(payload.get("content") or ""),
                "data": payload.get("data") if isinstance(payload.get("data"), dict) else {},
                "nested": True,
                "parent_call_id": parent_call_id,
            },
        )

    def _redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return redact_secrets(value)
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._redact_value(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._redact_value(item) for key, item in value.items()}
        return value


__all__ = ["CodeModeRuntime"]
