from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.ai import AIMessage, ChatRequest, MessageRole, ModelResponse, ModelUsage, ToolChoice

from .contracts import AgentEvent, AgentEventKind, AgentSession, AgentStatus
from .context_state import WorldStateEnvelope
from .memory_pipeline import MemoryPipeline
from .memory_store import (
    MemoryCandidate,
    MemoryCategory,
    MemoryEvidence,
    MemoryExtraction,
    MemoryRecord,
    MemoryScope,
    MemoryStore,
    redact_secrets,
    workspace_memory_key,
)
from .memory_tools import memory_tools
from .multi_agent_runtime import MultiAgentRuntime
from .storage import utc_now


_MEMORY_EXTRACTION_SYSTEM_PROMPT = (
    "You are Loom's long-term memory extraction stage. Inspect only the supplied observable conversation "
    "transcript and return strict JSON. Extract durable information that would materially improve future help. "
    "Give primary weight to the user's actual requests, corrections, decisions, constraints, and explicitly "
    "stated ways of working. Do not turn a one-off instruction into a broad stable preference unless the "
    "transcript supports that scope. Distinguish observed evidence and user decisions from assistant proposals, "
    "assumptions, or unfinished work. Never treat system/developer/runtime/tool instructions as user facts. "
    "Never store credentials, API keys, tokens, passwords, private keys, authentication cookies, or other secrets. "
    "Avoid ephemeral status updates, speculation, private chain-of-thought, and facts useful only inside the "
    "current turn. Use scope='global' only for stable cross-project user preferences or durable facts. Use "
    "scope='workspace' for project decisions, constraints, architecture, conventions, and workspace-specific facts. "
    "Return exactly one JSON object with keys summary and memories. memories must be an array of objects with "
    "text, scope, category, importance, evidence. category must be one of preference, fact, project, decision, "
    "constraint; importance is an integer 1..5. evidence should be a short grounded excerpt or close paraphrase "
    "from the supplied observable transcript that supports the memory. If nothing is worth remembering, return "
    "an empty memories array."
)


@dataclass(frozen=True, slots=True)
class MemoryExtractionResult:
    extraction: MemoryExtraction
    consolidated: tuple[MemoryRecord, ...]
    usage: ModelUsage


class MemoryRuntime(MultiAgentRuntime):
    """Durable long-term memory with asynchronous incremental extraction."""

    def __init__(
        self,
        *args,
        memory_store: MemoryStore | None = None,
        memory_context_limit: int = 6,
        memory_auto_extract: bool | None = None,
        memory_idle_seconds: float | None = None,
        memory_max_extraction_chars: int | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.memory_store = memory_store or MemoryStore(self.store.root.parent)
        self.memory_context_limit = max(1, min(16, int(memory_context_limit)))
        self.memory_max_extraction_chars = max(
            4_000,
            min(
                100_000,
                int(
                    memory_max_extraction_chars
                    if memory_max_extraction_chars is not None
                    else os.environ.get("LOOM_MEMORY_MAX_EXTRACTION_CHARS", "40000")
                ),
            ),
        )
        self.memory_idle_seconds = max(
            0.0,
            float(
                memory_idle_seconds
                if memory_idle_seconds is not None
                else os.environ.get("LOOM_MEMORY_IDLE_SECONDS", "45")
            ),
        )
        self.memory_auto_extract = (
            _env_bool("LOOM_MEMORY_AUTO_EXTRACT", True)
            if memory_auto_extract is None
            else bool(memory_auto_extract)
        )
        self._memory_pipeline: MemoryPipeline | None = None
        self._memory_backlog_scheduled = False

        for tool in memory_tools(self.memory_store):
            if self.tools.get(tool.name) is None:
                self.tools.register(tool)

        if self.memory_auto_extract:
            self._memory_pipeline = MemoryPipeline(
                self._process_memory_session,
                idle_seconds=self.memory_idle_seconds,
            )
            self.subscribe(self._memory_pipeline.on_event)

    def close(self) -> None:
        pipeline = self._memory_pipeline
        self._memory_pipeline = None
        if pipeline is not None:
            pipeline.stop()
        super().close()

    def extract_memory_from_thread(
        self,
        session_id: str,
        *,
        max_messages: int = 80,
        consolidate: bool = True,
    ) -> MemoryExtractionResult:
        """Explicit/manual extraction retained for compatibility."""

        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                raise RuntimeError("cannot extract long-term memory while a turn is active")
            transcript = _memory_transcript(session.messages, max_messages=max_messages)
            if not transcript:
                raise ValueError("thread has no observable conversation to extract memory from")
            result = self._perform_memory_extraction(
                profile_id=session.profile_id,
                source_session_id=session.session_id,
                source_turn_id=session.current_turn_id,
                workspace=session.workspace_dir,
                transcript=transcript,
                consolidate=consolidate,
            )
            session.usage = _add_usage(session.usage, result.usage)
            self._record(
                session,
                AgentEventKind.MEMORY_EXTRACTED,
                data=_memory_extracted_event_data(result),
            )
            if consolidate:
                self._record(
                    session,
                    AgentEventKind.MEMORY_CONSOLIDATED,
                    data=_memory_consolidated_event_data(result),
                )
            return result

    def extract_memory_from_events(
        self,
        session_id: str,
        *,
        consolidate: bool = True,
    ) -> MemoryExtractionResult | None:
        """Incrementally extract durable events without holding the foreground turn lock."""

        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                raise RuntimeError("cannot extract long-term memory while a turn is active")
            events = self.store.events(session_id)
            if not events:
                return None
            state = self.memory_store.thread_state(session_id)
            start_index = 0
            if state.last_event_id:
                for index, event in enumerate(events):
                    if event.event_id == state.last_event_id:
                        start_index = index + 1
                        break
            pending = events[start_index:]
            completed_indices = [
                index
                for index, event in enumerate(pending)
                if event.kind is AgentEventKind.TURN_COMPLETED
            ]
            if not completed_indices:
                return None
            selected = pending[: completed_indices[-1] + 1]
            terminal = selected[-1]
            transcript = _memory_event_transcript(
                selected,
                max_chars=self.memory_max_extraction_chars,
            )
            profile_id = session.profile_id
            workspace = session.workspace_dir
            source_start_event_id = selected[0].event_id
            source_end_event_id = terminal.event_id
            source_turn_id = terminal.turn_id

        if not transcript:
            self.memory_store.mark_thread_success(
                session_id,
                last_event_id=source_end_event_id,
                last_turn_id=source_turn_id,
            )
            return None

        # Model work is outside the session execution lease. A user can begin a
        # new foreground turn while memory extraction is running.
        result = self._perform_memory_extraction(
            profile_id=profile_id,
            source_session_id=session_id,
            source_turn_id=source_turn_id,
            workspace=workspace,
            transcript=transcript,
            source_start_event_id=source_start_event_id,
            source_end_event_id=source_end_event_id,
            consolidate=consolidate,
        )
        self.memory_store.mark_thread_success(
            session_id,
            last_event_id=source_end_event_id,
            last_turn_id=source_turn_id,
        )
        self._append_background_memory_audit(
            session_id=session_id,
            source_turn_id=source_turn_id,
            result=result,
        )
        return result

    def _perform_memory_extraction(
        self,
        *,
        profile_id: str,
        source_session_id: str,
        source_turn_id: str,
        workspace: str,
        transcript: str,
        consolidate: bool,
        source_start_event_id: str = "",
        source_end_event_id: str = "",
    ) -> MemoryExtractionResult:
        request = ChatRequest(
            messages=(
                AIMessage(role=MessageRole.SYSTEM, content=_MEMORY_EXTRACTION_SYSTEM_PROMPT),
                AIMessage(
                    role=MessageRole.USER,
                    content=(
                        "Extract durable memory candidates from this redacted observable transcript.\n\n"
                        + transcript
                    ),
                ),
            ),
            tools=(),
            tool_choice=ToolChoice.NONE,
            temperature=0.0,
            max_output_tokens=2400,
        )
        response = self.platform.execute_chat(profile_id, request)
        if not isinstance(response, ModelResponse):
            raise TypeError("memory extraction model must return ModelResponse")
        if response.tool_calls:
            raise RuntimeError("memory extraction model returned unexpected tool calls")

        payload = _parse_memory_payload(response.text)
        candidates = _validate_candidates(payload.get("memories"))
        extraction = self.memory_store.add_extraction(
            source_session_id=source_session_id,
            source_turn_id=source_turn_id,
            workspace=workspace,
            summary=redact_secrets(str(payload.get("summary") or "").strip()),
            candidates=candidates,
            usage_total_tokens=response.usage.total_tokens,
            source_start_event_id=source_start_event_id,
            source_end_event_id=source_end_event_id,
        )
        consolidated = self.memory_store.consolidate_pending() if consolidate else ()
        return MemoryExtractionResult(
            extraction=extraction,
            consolidated=consolidated,
            usage=response.usage,
        )

    def _append_background_memory_audit(
        self,
        *,
        session_id: str,
        source_turn_id: str,
        result: MemoryExtractionResult,
    ) -> None:
        try:
            self.store.append_event(
                AgentEvent(
                    event_id=str(uuid.uuid4()),
                    session_id=session_id,
                    turn_id=source_turn_id,
                    kind=AgentEventKind.MEMORY_EXTRACTED,
                    created_at=utc_now(),
                    data=_memory_extracted_event_data(result),
                )
            )
            if result.consolidated:
                self.store.append_event(
                    AgentEvent(
                        event_id=str(uuid.uuid4()),
                        session_id=session_id,
                        turn_id=source_turn_id,
                        kind=AgentEventKind.MEMORY_CONSOLIDATED,
                        created_at=utc_now(),
                        data=_memory_consolidated_event_data(result),
                    )
                )
        except Exception:
            # Memory state is already committed. Audit failure must not make the
            # same source turn eligible for extraction again.
            return

    def consolidate_memory(
        self,
        *,
        session_id: str | None = None,
        limit: int = 256,
    ) -> tuple[MemoryRecord, ...]:
        records = self.memory_store.consolidate_pending(limit=limit)
        if session_id is not None:
            lock = self._session_lock(session_id)
            with lock:
                session = self.store.load(session_id)
                self._record(
                    session,
                    AgentEventKind.MEMORY_CONSOLIDATED,
                    data={
                        "memory_ids": [record.memory_id for record in records],
                        "count": len(records),
                    },
                )
        return records

    def search_memory(
        self,
        session_id: str,
        query: str,
        *,
        limit: int = 8,
    ) -> tuple[MemoryRecord, ...]:
        session = self.store.load(session_id)
        return self.memory_store.search(query, workspace=session.workspace_dir, limit=limit)

    def read_memory(
        self,
        session_id: str,
        memory_id: str,
        *,
        evidence_limit: int = 20,
    ) -> tuple[MemoryRecord, tuple[MemoryEvidence, ...]] | None:
        session = self.store.load(session_id)
        record = self.memory_store.get_visible(memory_id, workspace=session.workspace_dir)
        if record is None:
            return None
        return record, self.memory_store.evidence(record.memory_id, limit=evidence_limit)

    def list_memory(self, session_id: str, *, limit: int = 100) -> tuple[MemoryRecord, ...]:
        session = self.store.load(session_id)
        return self.memory_store.list_records(workspace=session.workspace_dir, limit=limit)

    def memory_status(self, session_id: str) -> dict[str, object]:
        self._ensure_memory_backlog_scheduled()
        session = self.store.load(session_id)
        data: dict[str, object] = dict(
            self.memory_store.counts(workspace=session.workspace_dir)
        )
        state = self.memory_store.thread_state(session_id)
        data.update(
            {
                "auto_extract": self.memory_auto_extract,
                "pending_jobs": (
                    self._memory_pipeline.pending_count()
                    if self._memory_pipeline is not None
                    else 0
                ),
                "last_event_id": state.last_event_id,
                "last_turn_id": state.last_turn_id,
                "last_success_at": state.last_success_at,
                "failure_count": state.failure_count,
                "retry_at": state.retry_at,
                "last_error": state.last_error,
            }
        )
        return data

    def forget_memory(self, session_id: str, memory_id: str) -> bool:
        lock = self._session_lock(session_id)
        with lock:
            session = self.store.load(session_id)
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                raise RuntimeError("cannot forget long-term memory while a turn is active")
            record = self.memory_store.get(memory_id)
            if record is None:
                return False
            if (
                record.scope is MemoryScope.WORKSPACE
                and record.scope_key != workspace_memory_key(session.workspace_dir)
            ):
                raise PermissionError("memory belongs to a different workspace")
            forgotten = self.memory_store.delete(record.memory_id)
            if forgotten:
                self._record(
                    session,
                    AgentEventKind.MEMORY_FORGOTTEN,
                    data={
                        "memory_id": record.memory_id,
                        "scope": record.scope.value,
                        "category": record.category.value,
                    },
                )
            return forgotten

    def _request_context_messages(
        self,
        session: AgentSession,
        step,
        envelope: WorldStateEnvelope,
    ) -> tuple[AIMessage, ...]:
        self._ensure_memory_backlog_scheduled()
        base = super()._request_context_messages(session, step, envelope)
        records = self.memory_store.summary_records(
            workspace=session.workspace_dir,
            limit=self.memory_context_limit,
        )
        if not records:
            return base
        return (
            *base,
            AIMessage(
                role=MessageRole.SYSTEM,
                name="loom_memory",
                content=_render_memory_summary(records),
            ),
        )

    def _process_memory_session(self, session_id: str) -> None:
        pipeline = self._memory_pipeline
        try:
            session = self.store.load(session_id)
            if session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
                if pipeline is not None:
                    pipeline.schedule(session_id, delay=self.memory_idle_seconds)
                return
            self.extract_memory_from_events(session_id)
        except FileNotFoundError:
            return
        except Exception as exc:
            delay = self.memory_store.mark_thread_failure(session_id, str(exc))
            if pipeline is not None:
                pipeline.schedule(session_id, delay=delay)

    def _ensure_memory_backlog_scheduled(self) -> None:
        if self._memory_backlog_scheduled:
            return
        self._memory_backlog_scheduled = True
        pipeline = self._memory_pipeline
        if pipeline is None or not self.store.root.is_dir():
            return

        now = time.time()
        for directory in self.store.root.iterdir():
            if not directory.is_dir() or not (directory / "session.json").is_file():
                continue
            session_id = directory.name
            try:
                events = self.store.events(session_id)
                terminal = next(
                    (
                        event
                        for event in reversed(events)
                        if event.kind is AgentEventKind.TURN_COMPLETED
                    ),
                    None,
                )
                if terminal is None:
                    continue
                state = self.memory_store.thread_state(session_id)
                if state.last_event_id == terminal.event_id:
                    continue
                delay = max(0.0, state.retry_at - now) if state.retry_at else 0.0
                pipeline.schedule(session_id, delay=delay)
            except Exception:
                continue


def _memory_extracted_event_data(result: MemoryExtractionResult) -> dict[str, object]:
    extraction = result.extraction
    return {
        "extraction_id": extraction.extraction_id,
        "source_turn_id": extraction.source_turn_id,
        "source_start_event_id": extraction.source_start_event_id,
        "source_end_event_id": extraction.source_end_event_id,
        "candidate_count": extraction.candidate_count,
        "summary": extraction.summary,
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "total_tokens": result.usage.total_tokens,
        },
    }


def _memory_consolidated_event_data(result: MemoryExtractionResult) -> dict[str, object]:
    return {
        "extraction_id": result.extraction.extraction_id,
        "memory_ids": [record.memory_id for record in result.consolidated],
        "count": len(result.consolidated),
    }


def _render_memory_summary(records: tuple[MemoryRecord, ...]) -> str:
    lines = [
        "LOOM_MEMORY_SUMMARY v2",
        "Long-term memory is advisory and may be stale. Current user instructions, runtime state, and observed "
        "tool results always take precedence.",
        "Use search_memory when prior project history, preferences, constraints, or decisions may materially help. "
        "Use read_memory when you need the full memory and its provenance before relying on it.",
        "Summary:",
    ]
    total = sum(len(line) for line in lines)
    for record in records:
        text = " ".join(record.text.split())
        if len(text) > 520:
            text = text[:517].rstrip() + "..."
        line = (
            f"- [{record.scope.value}/{record.category.value}] "
            f"{text} (memory_id={record.memory_id})"
        )
        if total + len(line) > 6000:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _memory_transcript(messages: list[AIMessage], *, max_messages: int) -> str:
    eligible = [
        message
        for message in messages
        if message.role in {MessageRole.USER, MessageRole.ASSISTANT, MessageRole.TOOL}
    ]
    selected = eligible[-max(1, min(240, int(max_messages))):]
    rendered: list[str] = []
    total = 0
    for message in selected:
        body = _observable_message_text(message)
        if not body:
            continue
        label = message.role.value.upper()
        if message.role is MessageRole.TOOL and message.name:
            label += f"[{message.name}]"
        chunk = redact_secrets(f"{label}: {body}")[:6000]
        if total + len(chunk) > 60_000:
            break
        rendered.append(chunk)
        total += len(chunk)
    return "\n\n".join(rendered)


def _memory_event_transcript(
    events: tuple[AgentEvent, ...] | list[AgentEvent],
    *,
    max_chars: int,
) -> str:
    rendered: list[str] = []
    total = 0
    for event in events:
        body = ""
        label = ""
        data = event.data
        if event.kind is AgentEventKind.USER_MESSAGE:
            label = "USER"
            body = str(data.get("text") or "")
        elif event.kind is AgentEventKind.MODEL_RESPONSE:
            label = "ASSISTANT"
            body = str(data.get("text") or "")
            calls = data.get("tool_calls")
            if isinstance(calls, list) and calls:
                body = (
                    f"{body}\nOBSERVABLE_TOOL_CALLS: "
                    f"{json.dumps(calls, ensure_ascii=False)}"
                ).strip()
        elif event.kind in {AgentEventKind.TOOL_COMPLETED, AgentEventKind.TOOL_FAILED}:
            tool = str(data.get("tool") or "")
            label = f"TOOL[{tool}]" if tool else "TOOL"
            body = str(data.get("content") or "")
        elif event.kind is AgentEventKind.TURN_DIFF_UPDATED:
            label = "FILE_CHANGES"
            paths = data.get("paths")
            if isinstance(paths, list) and paths:
                body = json.dumps(paths, ensure_ascii=False)
        if not body:
            continue
        chunk = redact_secrets(f"{label}: {body}")[:6000]
        if total + len(chunk) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                rendered.append(chunk[:remaining])
            break
        rendered.append(chunk)
        total += len(chunk)
    return "\n\n".join(rendered)


def _observable_message_text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        body = message.content
    else:
        parts: list[str] = []
        for part in message.content:
            text = getattr(part, "text", "")
            if text:
                parts.append(str(text))
            elif getattr(part, "image_url", ""):
                parts.append("[image omitted]")
        body = "\n".join(parts)
    if message.role is MessageRole.ASSISTANT and message.tool_calls:
        calls = [
            {"name": call.name, "arguments": call.arguments}
            for call in message.tool_calls
        ]
        suffix = json.dumps(calls, ensure_ascii=False)
        body = (
            f"{body}\nOBSERVABLE_TOOL_CALLS: {suffix}"
            if body
            else f"OBSERVABLE_TOOL_CALLS: {suffix}"
        )
    return body.strip()


def _parse_memory_payload(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise RuntimeError("memory extraction model returned an empty response")
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("memory extraction response did not contain a JSON object")
    try:
        payload = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"memory extraction returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("memory extraction JSON root must be an object")
    return payload


def _validate_candidates(value: Any) -> tuple[MemoryCandidate, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RuntimeError("memory extraction 'memories' must be an array")
    if len(value) > 64:
        raise RuntimeError("memory extraction returned more than 64 candidates")
    candidates: list[MemoryCandidate] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise RuntimeError(f"memory candidate {index} must be an object")
        try:
            candidates.append(
                MemoryCandidate(
                    text=str(item.get("text") or ""),
                    scope=MemoryScope(str(item.get("scope") or "")),
                    category=MemoryCategory(str(item.get("category") or "")),
                    importance=int(item.get("importance") or 3),
                    evidence=str(item.get("evidence") or ""),
                )
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid memory candidate {index}: {exc}") from exc
    return tuple(candidates)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().casefold() not in {"0", "false", "off", "no"}


def _add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


__all__ = ["MemoryExtractionResult", "MemoryRuntime"]
