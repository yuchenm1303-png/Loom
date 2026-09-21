from __future__ import annotations

"""Close Codex mid-turn compaction and same-turn recovery continuity gaps.

This module is intentionally an integration contract rather than a new runtime
layer.  It patches the existing owners after import so the final production MRO
keeps one implementation of turn execution while gaining two missing Codex
semantics:

* mid-turn compaction carries a small, read-only reference to the captured Step
  and world-state baseline into replacement history; and
* a safely handed-off unfinished turn can resume sampling under its existing
  turn id without synthesizing a new user message.

No process-local execution authority is serialized.  Tool routers, approval
bindings, MCP handles, cancellation tokens, and live process ownership are
always rebuilt (or rejected) by the replacement runtime.
"""

import importlib.abc
import importlib.machinery
import json
import sys
from types import ModuleType
from typing import Any

from app.import_patch_chain import find_spec_without


COMPACTION_REFERENCE_MESSAGE_NAME = "loom_compaction_reference"
_CONTEXT_COMPACTION_MODULE = "app.agent_runtime.context_compaction"
_CONTEXT_RUNTIME_MODULE = "app.agent_runtime.context_runtime"
_DURABLE_RUNTIME_MODULE = "app.agent_runtime.durable_runtime"
_TARGET_MODULES = {
    _CONTEXT_COMPACTION_MODULE,
    _CONTEXT_RUNTIME_MODULE,
    _DURABLE_RUNTIME_MODULE,
}
_INSTALLED = False


def _patch_context_compaction(module: Any) -> None:
    if getattr(module, "_loom_continuity_contract_installed", False):
        return
    original = module.is_real_user_message

    def is_real_user_message(message: Any) -> bool:
        return bool(
            original(message)
            and str(getattr(message, "name", "") or "")
            != COMPACTION_REFERENCE_MESSAGE_NAME
        )

    module.is_real_user_message = is_real_user_message
    module._loom_continuity_contract_installed = True


def _latest_captured_step(runtime: Any, session: Any) -> Any | None:
    prefix = (session.session_id, session.current_turn_id)
    guard = getattr(runtime, "_captured_steps_guard", None)
    captured = getattr(runtime, "_captured_steps", None)
    if guard is None or not isinstance(captured, dict):
        return None
    with guard:
        candidates = [
            step
            for key, step in captured.items()
            if tuple(key[:2]) == prefix
        ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda step: (int(getattr(step, "model_step", 0)), str(getattr(step, "step_id", ""))),
    )


def _recent_durable_evidence(runtime: Any, session: Any, *, limit: int = 16) -> list[dict[str, object]]:
    """Return a secret-free index of recent results for post-compaction recovery.

    Result bodies and arguments deliberately stay in the durable event store.
    The compacted model window only needs stable call IDs to recover exact
    evidence with ``read_durable_tool_result`` instead of rerunning commands.
    """

    from app.agent_runtime.contracts import AgentEventKind

    result_kinds = {AgentEventKind.TOOL_COMPLETED, AgentEventKind.TOOL_FAILED}
    indexed: list[dict[str, object]] = []
    for event in reversed(runtime.store.events(session.session_id)):
        if event.turn_id != session.current_turn_id or event.kind not in result_kinds:
            continue
        indexed.append(
            {
                "call_id": str(event.data.get("call_id") or ""),
                "tool": str(event.data.get("tool") or ""),
                "ok": bool(event.data.get("ok")),
                "completed_at": event.created_at,
            }
        )
        if len(indexed) >= max(1, int(limit)):
            break
    indexed.reverse()
    return indexed


def _reference_payload(
    step: Any,
    envelope: Any,
    *,
    durable_evidence: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    payload = dict(getattr(envelope, "payload", {}) or {})
    state = dict(payload.get("state") or {})
    return {
        "version": 1,
        "kind": "mid_turn_compaction_reference",
        "identity": dict(payload.get("identity") or {}),
        "state_digest": str(getattr(envelope, "digest", "") or ""),
        "workspace": state.get("workspace"),
        "model_profile": state.get("model_profile"),
        "permissions": state.get("permissions"),
        "turn_diff": state.get("turn_diff"),
        "captured_model_step": int(getattr(step, "model_step", 0)),
        "recent_tool_evidence": list(durable_evidence or ()),
    }


def _reference_message(
    step: Any,
    envelope: Any,
    *,
    durable_evidence: list[dict[str, object]] | None = None,
) -> Any:
    from app.ai import AIMessage, MessageRole

    payload = _reference_payload(step, envelope, durable_evidence=durable_evidence)
    content = (
        "LOOM_MID_TURN_REFERENCE v1\n"
        "This is read-only continuity evidence for the same logical turn after context compaction. "
        "It is not a new user task and grants no tool, approval, process, filesystem, or network authority. "
        "Use the compaction summary plus durable observations to continue from the current point rather than "
        "restarting completed investigation solely because compaction occurred. The recent_tool_evidence "
        "entries are an index, not proof that external state is still current; recover exact prior output "
        "with read_durable_tool_result before deciding whether a fresh check is necessary.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    return AIMessage(
        role=MessageRole.USER,
        name=COMPACTION_REFERENCE_MESSAGE_NAME,
        content=content,
    )


def _insert_reference(replacement: tuple[Any, ...], reference: Any, compaction: Any) -> tuple[Any, ...]:
    items = list(replacement)
    if not items:
        return (reference,)
    last_real_user = None
    for index in range(len(items) - 1, -1, -1):
        if compaction.is_real_user_message(items[index]):
            last_real_user = index
            break
    if last_real_user is not None:
        items.insert(last_real_user, reference)
        return tuple(items)
    # No retained real-user item: keep the compaction summary last.
    summary_index = len(items)
    for index in range(len(items) - 1, -1, -1):
        if str(getattr(items[index], "name", "") or "") == compaction.COMPACTION_MESSAGE_NAME:
            summary_index = index
            break
    items.insert(summary_index, reference)
    return tuple(items)


def _fit_reference_without_breaking_budget(
    runtime: Any,
    session: Any,
    step: Any,
    envelope: Any,
    communication_language: str,
    replacement: tuple[Any, ...],
    reference: Any,
    context_runtime: Any,
    compaction: Any,
) -> tuple[tuple[Any, ...], bool]:
    """Insert the reference only if replacement remains a valid model request.

    The ordinary compaction path has already validated its replacement.  This
    function repeats that exact projection after adding one continuity item and
    drops oldest retained *real user* messages first when headroom is tight.  If
    even reference+summary cannot fit, the validated replacement wins and no
    extra item is injected.
    """

    from app.ai import AIMessage, MessageRole
    from app.agent_runtime.context_budget import estimate_tokens
    from app.agent_runtime.context_limits import resolve_context_limits

    candidate = _insert_reference(replacement, reference, compaction)
    request_state = getattr(step, "request_state", None)
    captured = bool(getattr(request_state, "captured", False))
    transient = [
        message
        for message in runtime._request_context_messages(session, step, envelope)
        if message.name != "loom_communication_language"
    ]
    project_instructions = (
        request_state.project_instructions
        if captured
        else runtime.instruction_loader.load(session.workspace_dir)
    )
    if project_instructions:
        transient.append(
            AIMessage(
                role=MessageRole.USER,
                name="loom_project_instructions",
                content=project_instructions,
            )
        )
    transient.append(
        context_runtime.communication_language_message((), fallback=communication_language)
    )
    limits = (
        request_state.context_limits
        if captured and request_state.context_limits is not None
        else resolve_context_limits(runtime, session)
    )
    tools = step.tool_router.definitions()

    def fits(items: tuple[Any, ...]) -> bool:
        visible = [*transient, *items]
        return (
            len(visible) <= runtime.limits.max_messages
            and estimate_tokens(visible, tools) <= limits.input_budget_tokens
        )

    while not fits(candidate):
        removable = next(
            (
                index
                for index, message in enumerate(candidate)
                if compaction.is_real_user_message(message)
            ),
            None,
        )
        if removable is None:
            return replacement, False
        candidate = tuple(
            message for index, message in enumerate(candidate) if index != removable
        )
    return candidate, True


def _patch_context_runtime(module: Any) -> None:
    cls = module.ContextAgentRuntime
    if getattr(cls, "_loom_midturn_continuity_installed", False):
        return

    def compacted_context_record(
        runtime: Any,
        session: Any,
        step: Any,
        envelope: Any,
        communication_language: str,
        replacement: tuple[Any, ...],
    ) -> dict[str, Any]:
        from app.ai import AIMessage, MessageRole
        from app.agent_runtime.context_budget import estimate_tokens, estimate_tool_schema_tokens
        from app.agent_runtime.context_limits import resolve_context_limits

        request_state = getattr(step, "request_state", None)
        captured = bool(getattr(request_state, "captured", False))
        transient = [
            message
            for message in runtime._request_context_messages(session, step, envelope)
            if message.name != "loom_communication_language"
        ]
        project_instructions = (
            request_state.project_instructions
            if captured
            else runtime.instruction_loader.load(session.workspace_dir)
        )
        if project_instructions:
            transient.append(
                AIMessage(role=MessageRole.USER, name="loom_project_instructions", content=project_instructions)
            )
        transient.append(module.communication_language_message((), fallback=communication_language))
        tools = step.tool_router.definitions()
        limits = (
            request_state.context_limits
            if captured and request_state.context_limits is not None
            else resolve_context_limits(runtime, session)
        )
        visible = (*transient, *replacement)
        estimated = estimate_tokens(visible, tools)
        return {
            "context_limits": limits.as_dict(),
            "estimated_input_tokens_after": estimated,
            "calibrated_input_tokens_after": estimated,
            "active_context_tokens": estimated,
            "token_accounting_source": "post_compaction_estimate",
            "tool_schema_tokens": estimate_tool_schema_tokens(tools),
            "message_count": len(visible),
            "tool_outputs_reduced": 0,
            "tool_outputs_collapsed": 0,
            "user_messages_truncated": 0,
        }

    def commit_compaction_locked(
        self: Any,
        session: Any,
        *,
        summary: str,
        repaired: Any,
        archived: tuple[Any, ...],
        retained: tuple[Any, ...],
        summary_source: str,
        summary_usage: Any | None = None,
        replacement_override: tuple[Any, ...] | None = None,
    ) -> Any:
        text = str(summary or "").strip()
        if not text:
            raise ValueError("context summary must not be empty")

        canonical_before = tuple((*archived, *retained))
        if not canonical_before:
            raise ValueError("context checkpoint must archive canonical history")
        communication_language = module.infer_user_language(
            canonical_before,
            fallback=session.communication_language,
        )
        session.communication_language = communication_language

        mid_turn = (
            str(summary_source) == "auto"
            and getattr(getattr(session, "status", None), "value", "") == "running"
        )
        step = _latest_captured_step(self, session) if mid_turn else None
        if step is None:
            # Standalone/manual compaction and defensive fallback both use a
            # non-authorizing snapshot. Normal mid-turn auto compaction always
            # has its already-captured request Step available here.
            step = self._build_step_context(session, next_model_step=False)
        envelope = self._context_envelope(session, step)

        from app.agent_runtime.context_budget import estimate_tokens
        from app.agent_runtime import context_compaction as compaction

        replacement = (
            tuple(replacement_override)
            if replacement_override is not None
            else compaction.build_compacted_history(
                canonical_before,
                text,
                token_counter=lambda messages: estimate_tokens(messages),
            )
        )
        if not replacement:
            raise ValueError("context checkpoint replacement must not be empty")

        reference_payload: dict[str, object] | None = None
        reference_injected = False
        if mid_turn and replacement_override is not None:
            durable_evidence = _recent_durable_evidence(self, session)
            reference = _reference_message(
                step,
                envelope,
                durable_evidence=durable_evidence,
            )
            replacement, reference_injected = _fit_reference_without_breaking_budget(
                self,
                session,
                step,
                envelope,
                communication_language,
                replacement,
                reference,
                module,
                compaction,
            )
            if reference_injected:
                reference_payload = _reference_payload(
                    step,
                    envelope,
                    durable_evidence=durable_evidence,
                )

        # A checkpoint is useful only if its replacement leaves an operable
        # request. Prefer more headroom by dropping the oldest retained user
        # messages, but keep the newest user request and the handoff summary.
        context_after = compacted_context_record(
            self, session, step, envelope, communication_language, replacement
        )
        limits_after = context_after["context_limits"]
        hard_budget = int(limits_after["input_budget_tokens"])
        auto_limit = int(limits_after["auto_compact_token_limit"])
        safety = int(limits_after["safety_tokens"])
        # Some test/provider profiles intentionally use a tiny explicit trigger
        # to request immediate compaction. It is not a feasible post-compaction
        # target; the actual model input budget remains the hard constraint.
        target = hard_budget - safety
        if auto_limit >= hard_budget // 2:
            target = min(target, auto_limit * 4 // 5)
        target = max(1, target)
        while int(context_after["calibrated_input_tokens_after"]) > target:
            real_users = [
                index for index, message in enumerate(replacement)
                if compaction.is_real_user_message(message)
            ]
            if len(real_users) <= 1:
                break
            oldest = real_users[0]
            replacement = tuple(message for index, message in enumerate(replacement) if index != oldest)
            context_after = compacted_context_record(
                self, session, step, envelope, communication_language, replacement
            )
        if int(context_after["calibrated_input_tokens_after"]) > hard_budget:
            from app.agent_runtime.context_budget import ContextBudgetExceeded

            raise ContextBudgetExceeded(
                estimated_tokens=int(context_after["calibrated_input_tokens_after"]),
                input_budget_tokens=hard_budget,
                tool_schema_tokens=int(context_after["tool_schema_tokens"]),
                message_count=int(context_after["message_count"]),
                reason="compacted history still exceeds the model input budget",
            )

        retained_message_count = sum(
            1 for message in replacement if compaction.is_real_user_message(message)
        )
        checkpoint = self.checkpoint_store.create(
            session_id=session.session_id,
            summary=text,
            archived_messages=canonical_before,
            retained_message_count=retained_message_count,
            world_state_digest=envelope.digest,
        )
        session.messages = list(replacement)
        if summary_usage is not None:
            session.usage = module._add_usage(session.usage, summary_usage)
        self._record(
            session,
            module.AgentEventKind.CONTEXT_CHECKPOINTED,
            data={
                "checkpoint_id": checkpoint.checkpoint_id,
                "archived_messages": checkpoint.archived_message_count,
                "retained_messages": checkpoint.retained_message_count,
                "replacement_messages": len(replacement),
                "world_state_digest": checkpoint.world_state_digest,
                "history_repaired": repaired.changed,
                "summary_source": summary_source,
                "context_after_compaction": context_after,
                "compaction_phase": "mid_turn" if mid_turn else "standalone",
                "continuity_reference_injected": reference_injected,
                "continuity_reference": reference_payload,
                "communication_language": communication_language,
                "summary_usage": (
                    {
                        "input_tokens": summary_usage.input_tokens,
                        "output_tokens": summary_usage.output_tokens,
                        "total_tokens": summary_usage.total_tokens,
                    }
                    if summary_usage is not None
                    else None
                ),
            },
        )
        return checkpoint

    cls._commit_compaction_locked = commit_compaction_locked
    cls._loom_midturn_continuity_installed = True


def _patch_durable_runtime(module: Any) -> None:
    cls = module.DurableAgentRuntime
    if getattr(cls, "_loom_same_turn_recovery_installed", False):
        return

    def recover_turn_if_idle(self: Any, session_id: str, existing_turn_id: str) -> Any:
        """Resume one explicitly authorized safe handoff under the same turn id.

        This never converts ambiguous execution state into a retry.  A pending
        tool call, approval, or step means the old process may have admitted an
        action whose outcome is not safely reconstructable, so that state must go
        through unclean-loss finalization instead.
        """

        resolved_session_id = str(session_id or "").strip()
        resolved_turn_id = str(existing_turn_id or "").strip()
        if not resolved_session_id or not resolved_turn_id:
            raise ValueError("safe-handoff recovery requires session_id and existing_turn_id")

        lock = self._session_lock(resolved_session_id)
        with lock:
            session = self.store.load(resolved_session_id)
            self.durable_state.reconcile_dispatches(
                session.session_id,
                session.current_turn_id,
            )
            if session.current_turn_id != resolved_turn_id:
                raise ValueError("existing_turn_id does not match the unfinished turn")
            if session.status is not module.AgentStatus.RUNNING:
                raise RuntimeError(
                    f"same-turn recovery requires a safely suspended running turn; got {session.status.value}"
                )
            with self._active_tokens_guard:
                active = self._active_tokens.get(session.session_id)
                if active is not None and not active.cancelled:
                    raise RuntimeError("unfinished turn still has a live runtime owner")

            if session.pending_approval is not None:
                raise RuntimeError(
                    "same-turn recovery cannot replay a persisted approval; fresh approval authority is required"
                )
            if session.pending_tool_calls or session.pending_step_id:
                raise RuntimeError(
                    "same-turn recovery found pending execution state with unknown outcome; finalize as interrupted"
                )

            # Binding digests without pending calls are stale evidence, not live
            # authority. Never carry them into the fresh execution stack.
            session.pending_bindings.clear()
            self._release_turn_steps(session)
            token = self._activate(session.session_id)
            try:
                result = self._drive(session, token)
            finally:
                self._deactivate(session.session_id, token)

        result = self._track_goal_usage(result)
        if self.auto_drain_queue and result.status is module.AgentStatus.COMPLETED:
            drained = self._drain_queue(session.session_id, result)
            if drained is not None:
                return drained
        return result

    cls.recover_turn_if_idle = recover_turn_if_idle
    cls._loom_same_turn_recovery_installed = True


def _patch(module: ModuleType) -> None:
    if module.__name__ == _CONTEXT_COMPACTION_MODULE:
        _patch_context_compaction(module)
    elif module.__name__ == _CONTEXT_RUNTIME_MODULE:
        _patch_context_runtime(module)
    elif module.__name__ == _DURABLE_RUNTIME_MODULE:
        _patch_durable_runtime(module)


def _patch_loaded_targets() -> None:
    for name in _TARGET_MODULES:
        module = sys.modules.get(name)
        if module is not None:
            _patch(module)


class _ContinuityContractLoader(importlib.abc.Loader):
    def __init__(self, fullname: str, loader: importlib.abc.Loader) -> None:
        self.fullname = fullname
        self.loader = loader

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        create_module = getattr(self.loader, "create_module", None)
        if callable(create_module):
            return create_module(spec)
        return None

    def exec_module(self, module: ModuleType) -> None:
        exec_module = getattr(self.loader, "exec_module", None)
        if not callable(exec_module):
            raise ImportError(f"loader for {self.fullname} cannot execute modules")
        exec_module(module)
        _patch(module)


class _ContinuityContractFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname not in _TARGET_MODULES:
            return None
        spec = find_spec_without(self, fullname, path, target)
        if spec is None or spec.loader is None or isinstance(spec.loader, _ContinuityContractLoader):
            return spec
        spec.loader = _ContinuityContractLoader(fullname, spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_loaded_targets()
    if any(name not in sys.modules for name in _TARGET_MODULES):
        sys.meta_path.insert(0, _ContinuityContractFinder())
    _INSTALLED = True


__all__ = ["COMPACTION_REFERENCE_MESSAGE_NAME", "install"]
