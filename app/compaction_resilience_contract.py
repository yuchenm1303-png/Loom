from __future__ import annotations

"""Fail-soft compaction recovery for provider protocol violations.

Compaction is an internal text-only checkpoint task.  A provider that ignores
``tool_choice=none`` must never gain tool authority, and a run of malformed
compaction responses must not turn an otherwise recoverable long-running turn
into a terminal runtime failure.

The normal Codex-compatible model compaction path remains authoritative.  This
contract only activates after that path exhausts its bounded response-retry
budget.  It then creates a deterministic, read-only handoff summary from the
canonical transcript and commits it through the ordinary checkpoint path.  The
full canonical history is still archived by the checkpoint store.
"""

import importlib.abc
import importlib.machinery
import json
import sys
from types import ModuleType
from typing import Any, Sequence

from app.import_patch_chain import find_spec_without


_CONTEXT_BUDGET_MODULE = "app.agent_runtime.context_budget"
_CONTEXT_RUNTIME_MODULE = "app.agent_runtime.context_runtime"
_TARGET_MODULES = {_CONTEXT_BUDGET_MODULE, _CONTEXT_RUNTIME_MODULE}
_INSTALLED = False

_FALLBACK_HEADER = (
    "Deterministic Loom checkpoint created because the compaction provider did not return usable summary text. "
    "This is continuity state for the same task, not a new user request. Continue from the recorded progress and "
    "do not repeat completed investigation solely because compaction occurred."
)
_MAX_FALLBACK_CHARS = 12_000
_MAX_ENTRY_CHARS = 2_400


def _clip(text: str, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    if limit <= 32:
        return value[:limit]
    head = max(1, (limit - 24) // 2)
    tail = max(1, limit - 24 - head)
    return value[:head] + "\n[... clipped ...]\n" + value[-tail:]


def _content_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in tuple(content or ()):
        text = getattr(part, "text", None)
        if text:
            parts.append(str(text))
            continue
        if getattr(part, "image_url", None):
            parts.append("[image]")
    return "\n".join(parts)


def build_deterministic_compaction_summary(
    history: Sequence[Any],
    *,
    max_chars: int = _MAX_FALLBACK_CHARS,
) -> str:
    """Build a bounded local handoff that favors the newest execution evidence."""

    budget = max(512, int(max_chars))
    entries_reversed: list[str] = []
    used = len(_FALLBACK_HEADER) + 64

    for message in reversed(tuple(history)):
        role = str(getattr(getattr(message, "role", None), "value", getattr(message, "role", "unknown")))
        name = str(getattr(message, "name", "") or "").strip()
        label = role if not name else f"{role}:{name}"
        body = _clip(_content_text(message).strip(), _MAX_ENTRY_CHARS)

        calls = []
        for call in tuple(getattr(message, "tool_calls", ()) or ()):
            call_name = str(getattr(call, "name", "") or "tool")
            arguments = getattr(call, "arguments", {}) or {}
            try:
                raw_arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except Exception:
                raw_arguments = repr(arguments)
            calls.append(f"{call_name}({_clip(raw_arguments, 600)})")
        if calls:
            call_text = ", ".join(calls)
            body = (body + "\n" if body else "") + f"tool calls requested: {call_text}"

        if not body:
            continue
        entry = f"[{label}]\n{body}"
        entry = _clip(entry, _MAX_ENTRY_CHARS + 160)
        cost = len(entry) + 2
        if entries_reversed and used + cost > budget:
            break
        if not entries_reversed and used + cost > budget:
            entry = _clip(entry, max(128, budget - used))
            cost = len(entry) + 2
        entries_reversed.append(entry)
        used += cost
        if used >= budget:
            break

    entries_reversed.reverse()
    if not entries_reversed:
        entries_reversed.append("[continuity]\nNo textual execution evidence was available; preserve the retained user instructions and runtime state.")
    return _FALLBACK_HEADER + "\n\nRecent canonical progress:\n\n" + "\n\n".join(entries_reversed)


def _invalid_compaction_error(exc: BaseException) -> bool:
    text = str(exc or "").casefold()
    return (
        "context compaction model repeatedly returned unexpected tool calls" in text
        or "context compaction model repeatedly returned an empty summary" in text
        or "context compaction model returned unexpected tool calls" in text
        or "context compaction model returned an empty summary" in text
    )


def _projection_context(module: Any, runtime: Any, session: Any, step: Any, envelope: Any):
    from app.ai import AIMessage, MessageRole
    from app.agent_runtime.context_limits import resolve_context_limits
    from app.agent_runtime.response_language import communication_language_message, infer_user_language

    transient = [
        message
        for message in runtime._request_context_messages(session, step, envelope)
        if str(getattr(message, "name", "") or "") != "loom_communication_language"
    ]
    request_state = getattr(step, "request_state", None)
    captured = bool(getattr(request_state, "captured", False))
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
    communication_language = (
        request_state.communication_language
        if captured
        else infer_user_language(session.messages, fallback=session.communication_language)
    )
    session.communication_language = communication_language
    transient.append(
        communication_language_message(
            () if captured else session.messages,
            fallback=communication_language,
        )
    )
    limits = (
        request_state.context_limits
        if captured and request_state.context_limits is not None
        else resolve_context_limits(runtime, session)
    )
    tools = step.tool_router.definitions()
    return transient, communication_language, limits, tools


def _fit_fallback_replacement(
    module: Any,
    runtime: Any,
    session: Any,
    step: Any,
    repair: Any,
    summary: str,
) -> tuple[tuple[Any, ...], list[Any], str, Any, Any, int]:
    from app.ai import AIMessage
    from app.agent_runtime import context_compaction as compaction

    envelope = runtime._context_envelope(session, step)
    transient, communication_language, limits, tools = _projection_context(
        module, runtime, session, step, envelope
    )
    replacement = compaction.build_compacted_history(
        tuple(repair.messages),
        summary,
        token_counter=lambda messages: module.estimate_tokens(messages),
    )
    replacement = module._fit_replacement_message_limit(
        replacement,
        transient_count=len(transient),
        max_messages=runtime.limits.max_messages,
    )

    def projected_tokens(items: Sequence[Any]) -> int:
        return module.estimate_tokens([*transient, *items], tools)

    while (
        projected_tokens(replacement) > limits.input_budget_tokens
        or len(transient) + len(replacement) > runtime.limits.max_messages
    ) and len(replacement) > 1:
        removable = next(
            (
                index
                for index, message in enumerate(replacement[:-1])
                if compaction.is_real_user_message(message)
            ),
            None,
        )
        if removable is None:
            break
        replacement = tuple(
            message for index, message in enumerate(replacement) if index != removable
        )

    if projected_tokens(replacement) > limits.input_budget_tokens and replacement:
        summary_message = replacement[-1]
        original_content = str(getattr(summary_message, "content", "") or "")
        low, high = 64, len(original_content)
        best: str | None = None
        while low <= high:
            mid = (low + high) // 2
            candidate_message = AIMessage(
                role=summary_message.role,
                name=summary_message.name,
                content=original_content[:mid],
            )
            candidate = tuple((*replacement[:-1], candidate_message))
            if projected_tokens(candidate) <= limits.input_budget_tokens:
                best = original_content[:mid]
                low = mid + 1
            else:
                high = mid - 1
        if best is not None:
            replacement = tuple(
                (*replacement[:-1], AIMessage(role=summary_message.role, name=summary_message.name, content=best))
            )

    estimated_after = projected_tokens(replacement)
    if (
        estimated_after > limits.input_budget_tokens
        or len(transient) + len(replacement) > runtime.limits.max_messages
    ):
        raise module.ContextBudgetExceeded(
            estimated_tokens=estimated_after,
            input_budget_tokens=limits.input_budget_tokens,
            tool_schema_tokens=module.estimate_tool_schema_tokens(tools),
            message_count=len(transient) + len(replacement),
            reason="deterministic compaction fallback cannot fit the current model request budget",
        )
    return replacement, transient, communication_language, limits, tools, estimated_after


def _patch_context_budget(module: Any) -> None:
    if getattr(module, "_loom_compaction_resilience_installed", False):
        return
    original = module.prepare_context

    def prepare_context(runtime: Any, session: Any, step: Any, token: Any):
        try:
            return original(runtime, session, step, token)
        except RuntimeError as exc:
            if not _invalid_compaction_error(exc):
                raise

        # The model path has already exhausted its bounded retry budget.  Build a
        # local handoff from canonical history; never execute the returned calls.
        module._raise_if_cancelled(token)
        repair = module.repair_tool_history(
            tuple(session.messages),
            max_tool_result_chars=runtime.limits.max_tool_result_chars,
        )
        if not repair.messages:
            raise module.ContextBudgetExceeded(
                estimated_tokens=0,
                input_budget_tokens=0,
                tool_schema_tokens=0,
                message_count=0,
                reason="empty canonical history cannot be compacted",
            )
        summary = build_deterministic_compaction_summary(tuple(repair.messages))
        replacement, transient, communication_language, limits, tools, estimated_after = _fit_fallback_replacement(
            module, runtime, session, step, repair, summary
        )
        runtime._commit_compaction_locked(
            session,
            summary=summary,
            repaired=repair,
            archived=tuple(repair.messages),
            retained=(),
            summary_source="auto",
            summary_usage=None,
            replacement_override=replacement,
        )
        committed_visible = [*transient, *session.messages]
        committed_tokens = module.estimate_tokens(committed_visible, tools)
        metadata = module._metadata(
            envelope=runtime._context_envelope(session, step),
            communication_language=communication_language,
            limits=limits,
            tools=tools,
            estimated_before=committed_tokens,
            estimated_after=committed_tokens,
            active_context_tokens=committed_tokens,
            token_accounting_source="deterministic_fallback",
        )
        metadata.update(
            {
                "auto_compacted": True,
                "compaction_attempts": int(runtime.limits.model_retries) + 1,
                "compaction_trimmed_messages": 0,
                "compaction_fallback": "deterministic",
                "compaction_provider_response_invalid": True,
                "estimated_input_tokens_after": min(estimated_after, committed_tokens),
            }
        )
        return committed_visible, metadata

    module.prepare_context = prepare_context
    module._loom_compaction_resilience_installed = True


def _patch_context_runtime(module: Any) -> None:
    cls = module.ContextAgentRuntime
    if getattr(cls, "_loom_manual_compaction_resilience_installed", False):
        return
    original = cls.compact_context_with_model

    def compact_context_with_model(self: Any, session_id: str, *, keep_recent: int = 24):
        invalid: RuntimeError | None = None
        attempts = max(1, int(self.limits.model_retries) + 1)
        for _ in range(attempts):
            try:
                return original(self, session_id, keep_recent=keep_recent)
            except RuntimeError as exc:
                if not _invalid_compaction_error(exc):
                    raise
                invalid = exc

        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status in {module.AgentStatus.RUNNING, module.AgentStatus.WAITING_APPROVAL}:
                raise RuntimeError("cannot compact context while a turn is active")
            repaired, archived, retained = self._prepare_compaction_locked(
                session,
                keep_recent=keep_recent,
            )
            summary = build_deterministic_compaction_summary(tuple((*archived, *retained)))
            checkpoint = self._commit_compaction_locked(
                session,
                summary=summary,
                repaired=repaired,
                archived=archived,
                retained=retained,
                summary_source="model_fallback",
            )
        _ = invalid
        return checkpoint

    cls.compact_context_with_model = compact_context_with_model
    cls._loom_manual_compaction_resilience_installed = True


def _patch(module: ModuleType) -> None:
    if module.__name__ == _CONTEXT_BUDGET_MODULE:
        _patch_context_budget(module)
    elif module.__name__ == _CONTEXT_RUNTIME_MODULE:
        _patch_context_runtime(module)


def _patch_loaded_targets() -> None:
    for name in _TARGET_MODULES:
        module = sys.modules.get(name)
        if module is not None:
            _patch(module)


class _CompactionResilienceLoader(importlib.abc.Loader):
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


class _CompactionResilienceFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname not in _TARGET_MODULES:
            return None
        spec = find_spec_without(self, fullname, path, target)
        if spec is None or spec.loader is None or isinstance(spec.loader, _CompactionResilienceLoader):
            return spec
        spec.loader = _CompactionResilienceLoader(fullname, spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_loaded_targets()
    if any(name not in sys.modules for name in _TARGET_MODULES):
        sys.meta_path.insert(0, _CompactionResilienceFinder())
    _INSTALLED = True


__all__ = ["build_deterministic_compaction_summary", "install"]
