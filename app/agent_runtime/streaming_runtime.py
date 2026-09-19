from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from app.ai import AIMessage, MessageRole
from app.ai.execution_control import current_control
from app.ai.streaming_platform import ProviderStreamEvent, ProviderStreamEventKind

from .code_mode_runtime import CodeModeRuntime
from .contracts import AgentEvent, AgentEventKind, AgentSession
from .stickers import (
    StickerContext,
    StickerPreferences,
    StickerResult,
    StickerStreamSanitizer,
    _STICKER_OPT_IN_RE,
    _STICKER_OPT_OUT_RE,
    build_sticker_system_prompt,
    finalize_reply,
    is_catalog_or_test_request,
    reconcile_stream_reply,
)
from .storage import utc_now


_INLINE_STICKER_CONTROL_PREFIX = "[[AI_LEDGER_INLINE_STICKER:"
_COMPLETE_INLINE_STICKER_RE = re.compile(
    r"\[\[AI_LEDGER_INLINE_STICKER:[a-z0-9_]{2,48}\]\]",
    re.I,
)
_DAMAGED_INLINE_STICKER_RE = re.compile(
    r"(?<![\[A-Z0-9_])(?:\[\[AI|\[AI|\[IA|AI|IA)_LEDGER_INLINE_STICKER:"
    r"[a-z0-9_]{0,96}\]{0,2}",
    re.I,
)


def _strip_incomplete_sticker_control_fragments(text: str) -> str:
    """Remove truncated canonical sticker control data without touching valid markers."""

    source = str(text or "")
    # Provider near-misses are control data too. Never persist them as prose.
    # Preserve exact canonical markers for normal sticker materialization.
    source = _DAMAGED_INLINE_STICKER_RE.sub(
        lambda match: match.group(0)
        if _COMPLETE_INLINE_STICKER_RE.fullmatch(match.group(0))
        else "",
        source,
    )
    if "[[" not in source:
        return source

    prefix = _INLINE_STICKER_CONTROL_PREFIX
    prefix_upper = prefix.upper()
    source_upper = source.upper()
    output: list[str] = []
    cursor = 0

    while True:
        start = source_upper.find(prefix_upper, cursor)
        if start < 0:
            break

        complete = _COMPLETE_INLINE_STICKER_RE.match(source, start)
        if complete is not None:
            output.append(source[cursor:complete.end()])
            cursor = complete.end()
            continue

        output.append(source[cursor:start])
        end = start + len(prefix)
        key_chars = 0
        while end < len(source) and key_chars < 96:
            char = source[end]
            if not (char.isascii() and (char.isalnum() or char == "_")):
                break
            end += 1
            key_chars += 1
        closing = 0
        while end < len(source) and source[end] == "]" and closing < 2:
            end += 1
            closing += 1
        cursor = end

    output.append(source[cursor:])
    cleaned = "".join(output)

    # A provider can stop in the middle of the control prefix itself. That only
    # occurs at the reply boundary, so remove a trailing prefix fragment too.
    cleaned_upper = cleaned.upper()
    max_width = min(len(prefix_upper) - 1, len(cleaned_upper))
    for width in range(max_width, 1, -1):
        if cleaned_upper.endswith(prefix_upper[:width]):
            cleaned = cleaned[:-width]
            break
    return cleaned


class AgentStreamEventKind(str, Enum):
    ASSISTANT_TEXT_DELTA = "assistant_text_delta"
    TOOL_CALL_ARGUMENT_DELTA = "tool_call_argument_delta"
    MODEL_STREAM_COMPLETED = "model_stream_completed"


@dataclass(frozen=True, slots=True)
class AgentStreamEvent:
    session_id: str
    turn_id: str
    step_id: str
    kind: AgentStreamEventKind
    created_at: str
    data: dict[str, Any] = field(default_factory=dict)


AgentStreamListener = Callable[[AgentStreamEvent], None]


@dataclass(frozen=True, slots=True)
class _ModelStreamContext:
    session_id: str
    turn_id: str
    step_id: str
    profile_id: str


class StreamingAgentRuntime(CodeModeRuntime):
    """Runtime v2 top layer that correlates provider deltas with active model steps.

    Provider chunks are transient observable state. They are delivered through a
    separate event bus and are never appended to ``events.jsonl`` or canonical
    thread history. The normal MODEL_RESPONSE event remains the atomic durable
    commit boundary, so disconnecting a UI cannot corrupt a Turn.

    The AI Ledger inline-sticker protocol is attached here because this is the
    one layer that sees both the provider stream and the final durable response.
    The sticker module owns the ported policy; this class only supplies Loom
    context, forwards sanitized deltas, and commits the already-finalized text.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._stream_listener_guard = threading.RLock()
        self._stream_listeners: list[AgentStreamListener] = []
        self._stream_context = ContextVar("loom_stream_context", default=None)
        self._provider_streaming_enabled = False
        self._streaming_platform_ids: set[int] = set()

        self._sticker_guard = threading.RLock()
        self._sticker_streams: dict[tuple[str, str, str], StickerStreamSanitizer] = {}
        self._sticker_stream_text: dict[tuple[str, str, str], str] = {}
        self._sticker_diagnostics: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._sticker_final_by_turn: dict[tuple[str, str], str] = {}
        self._buffered_checkpoint_streams: set[tuple[str, str, str]] = set()
        self._sticker_preferences = StickerPreferences.from_env()

        super().__init__(*args, **kwargs)
        self._sticker_preferences = self._load_sticker_preferences()

        self._configure_streaming_platform(self.platform)

    def _configure_streaming_platform(self, platform: Any) -> None:
        identity = id(platform)
        if identity in self._streaming_platform_ids:
            return
        enable = getattr(platform, "enable_streaming", None)
        subscribe = getattr(platform, "subscribe_stream", None)
        if callable(enable) and callable(subscribe):
            enable()
            subscribe(self._on_provider_stream)
            self._streaming_platform_ids.add(identity)
            self._provider_streaming_enabled = True

    def set_session_model(self, session_id: str, platform: Any, *, reasoning=None) -> None:
        super().set_session_model(session_id, platform, reasoning=reasoning)
        self._configure_streaming_platform(platform)

    @property
    def provider_streaming_enabled(self) -> bool:
        return self._provider_streaming_enabled

    @contextmanager
    def _internal_model_stream_scope(self):
        """Prevent compaction/memory model deltas from becoming chat output.

        The provider event bus is shared by foreground and detached requests.
        Temporarily clearing correlation is the hard boundary: even if a stale
        foreground context survives an exceptional path, internal output has no
        user-visible session/turn/step to attach to.
        """

        binding = self._stream_context.set(None)
        try:
            yield
        finally:
            self._stream_context.reset(binding)

    def _sticker_preferences_path(self) -> Path | None:
        root = getattr(self.store, "root", None)
        if root is None:
            return None
        try:
            # FileAgentSessionStore.root is <LOOM_HOME>/agent_runtime/sessions.
            return Path(root).resolve().parent / "sticker-preferences.json"
        except (OSError, TypeError, ValueError):
            return None

    def _load_sticker_preferences(self) -> StickerPreferences:
        path = self._sticker_preferences_path()
        if path is None or not path.is_file():
            return StickerPreferences.from_env()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return StickerPreferences.from_env()
        return StickerPreferences.normalize(payload if isinstance(payload, dict) else None)

    def get_sticker_preferences(self) -> dict[str, Any]:
        with self._sticker_guard:
            return self._sticker_preferences.to_dict()

    def set_sticker_preferences(self, raw: Mapping[str, Any] | None) -> dict[str, Any]:
        preferences = StickerPreferences.normalize(raw)
        path = self._sticker_preferences_path()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            data = json.dumps(preferences.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
            try:
                temp.write_text(data + "\n", encoding="utf-8")
                os.replace(temp, path)
            finally:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
        with self._sticker_guard:
            self._sticker_preferences = preferences
        return preferences.to_dict()

    def _is_spawned_agent_session(self, session: AgentSession) -> bool:
        """Report whether this session is a spawned child rather than the user's chat.

        Stickers decorate a reply the user reads. A child agent's reply is consumed
        by its parent as data, so decorating it puts protocol markers into another
        agent's context instead of in front of a renderer.
        """

        graph = getattr(self, "agent_graph", None)
        if graph is None:
            return False
        try:
            node = graph.get(session.session_id)
        except Exception:
            return False
        return node is not None and bool(getattr(node, "parent_session_id", ""))

    def _sticker_context_for_session(self, session: AgentSession, *, streaming: bool) -> StickerContext:
        user_text = ""
        assistant_history: list[str] = []
        for message in session.messages:
            if not isinstance(message.content, str):
                continue
            if message.role is MessageRole.USER:
                user_text = message.content
            elif message.role is MessageRole.ASSISTANT:
                assistant_history.append(message.content)

        custom_instructions = str(getattr(session, "system_prompt", "") or "")
        try:
            project = self.instruction_loader.load(session.workspace_dir)
        except Exception:
            project = ""
        if project:
            custom_instructions = f"{custom_instructions}\n{project}".strip()

        # Preserve the worker's V244 gate without inventing a Loom-specific
        # policy. Current-turn explicit opt-in overrides a custom-instruction
        # opt-out; a current-turn opt-out is still handled by analyze_scene.
        current_opt_in = bool(_STICKER_OPT_IN_RE.search(user_text)) or is_catalog_or_test_request(user_text)
        custom_opt_out = bool(
            not current_opt_in
            and custom_instructions
            and _STICKER_OPT_OUT_RE.search(custom_instructions)
        )
        with self._sticker_guard:
            preferences = self._sticker_preferences
        allow_stickers = (
            not custom_opt_out
            and preferences.frequency > 0
            and not self._is_spawned_agent_session(session)
        )

        return StickerContext(
            user_text=user_text,
            assistant_history=tuple(assistant_history[-8:]),
            streaming=streaming,
            allow_stickers=allow_stickers,
        )

    def _prepare_model_request(self, session, step, token):
        messages, extra = super()._prepare_model_request(session, step, token)
        with self._sticker_guard:
            preferences = self._sticker_preferences
        context = self._sticker_context_for_session(
            session,
            streaming=self.provider_streaming_enabled,
        )
        sticker_prompt = build_sticker_system_prompt(preferences, context)
        insert_at = 0
        while insert_at < len(messages) and messages[insert_at].role is MessageRole.SYSTEM:
            insert_at += 1
        messages.insert(
            insert_at,
            AIMessage(
                role=MessageRole.SYSTEM,
                name="loom_inline_sticker_protocol",
                content=sticker_prompt,
            ),
        )
        merged_extra = dict(extra)
        merged_extra.update({
            "sticker_protocol": "ai_ledger_inline_sticker_v4_structured_sidecar",
            "sticker_preferences": preferences.to_dict(),
        })
        return messages, merged_extra

    def subscribe_stream(self, listener: AgentStreamListener) -> None:
        if not callable(listener):
            raise TypeError("runtime stream listener must be callable")
        with self._stream_listener_guard:
            self._stream_listeners.append(listener)

    def _emit_stream(
        self,
        context: _ModelStreamContext,
        kind: AgentStreamEventKind,
        data: dict[str, Any],
    ) -> None:
        json.dumps(data, ensure_ascii=False)
        event = AgentStreamEvent(
            session_id=context.session_id,
            turn_id=context.turn_id,
            step_id=context.step_id,
            kind=kind,
            created_at=utc_now(),
            data=dict(data),
        )
        with self._stream_listener_guard:
            listeners = tuple(self._stream_listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                continue

    def _sticker_stream_key(self, context: _ModelStreamContext) -> tuple[str, str, str]:
        return (context.session_id, context.turn_id, context.step_id)

    def _on_provider_stream(self, event: ProviderStreamEvent) -> None:
        control = current_control.get()
        if control is not None and control.cancelled:
            return
        context = self._stream_context.get()
        if not isinstance(context, _ModelStreamContext):
            # Detached model tasks such as memory extraction/compaction may use
            # the same platform but are not part of an active Agent model step.
            return
        if context.profile_id != event.profile_id:
            return

        key = self._sticker_stream_key(context)
        if event.kind is ProviderStreamEventKind.TEXT_DELTA:
            if not event.text_delta:
                return
            # A model that received private checkpoint context may echo it.
            # Buffer its text until the durable response sanitizer has proved
            # it safe; tool-call deltas remain independently streamable.
            with self._sticker_guard:
                if key in self._buffered_checkpoint_streams:
                    return
            with self._sticker_guard:
                sanitizer = self._sticker_streams.get(key)
            delta = sanitizer.push(event.text_delta) if sanitizer is not None else event.text_delta
            if delta:
                self._emit_stream(
                    context,
                    AgentStreamEventKind.ASSISTANT_TEXT_DELTA,
                    {"delta": delta, "profile_id": event.profile_id},
                )
            return

        if event.kind is ProviderStreamEventKind.TOOL_CALL_DELTA:
            self._emit_stream(
                context,
                AgentStreamEventKind.TOOL_CALL_ARGUMENT_DELTA,
                {
                    "profile_id": event.profile_id,
                    "index": event.tool_call_index,
                    "call_id": event.tool_call_id,
                    "tool": event.tool_name,
                    "arguments_delta": event.arguments_delta,
                },
            )
            return

        if event.kind is ProviderStreamEventKind.COMPLETED:
            with self._sticker_guard:
                sanitizer = self._sticker_streams.get(key)
            if sanitizer is not None:
                tail = sanitizer.finish()
                if tail:
                    self._emit_stream(
                        context,
                        AgentStreamEventKind.ASSISTANT_TEXT_DELTA,
                        {"delta": tail, "profile_id": event.profile_id},
                    )
                with self._sticker_guard:
                    self._sticker_stream_text[key] = sanitizer.value()
                    self._sticker_diagnostics[key] = sanitizer.diagnostics()
            self._emit_stream(
                context,
                AgentStreamEventKind.MODEL_STREAM_COMPLETED,
                {
                    "profile_id": event.profile_id,
                    "finish_reason": event.finish_reason,
                    "response_id": event.response_id,
                    "usage": {
                        "input_tokens": event.usage.input_tokens,
                        "output_tokens": event.usage.output_tokens,
                        "total_tokens": event.usage.total_tokens,
                    },
                },
            )

    def _finalize_sticker_model_text(
        self,
        session: AgentSession,
        *,
        step_id: str,
        raw_text: str,
    ) -> StickerResult:
        with self._sticker_guard:
            preferences = self._sticker_preferences
        context = self._sticker_context_for_session(
            session,
            streaming=self.provider_streaming_enabled,
        )
        clean_raw_text = _strip_incomplete_sticker_control_fragments(raw_text)
        provider_result = finalize_reply(clean_raw_text, preferences, context)
        key = (session.session_id, session.current_turn_id, step_id)
        streamed_text = ""
        with self._sticker_guard:
            sanitizer = self._sticker_streams.get(key)
        if sanitizer is not None:
            tail = sanitizer.finish()
            active = self._stream_context.get()
            if tail and isinstance(active, _ModelStreamContext) and self._sticker_stream_key(active) == key:
                self._emit_stream(
                    active,
                    AgentStreamEventKind.ASSISTANT_TEXT_DELTA,
                    {"delta": tail, "profile_id": session.profile_id},
                )
            streamed_text = sanitizer.value()
            with self._sticker_guard:
                self._sticker_stream_text[key] = streamed_text
                self._sticker_diagnostics[key] = sanitizer.diagnostics()
        else:
            with self._sticker_guard:
                streamed_text = self._sticker_stream_text.get(key, "")

        finalized = (
            reconcile_stream_reply(provider_result, streamed_text)
            if self.provider_streaming_enabled and streamed_text
            else provider_result
        )
        with self._sticker_guard:
            diagnostics = dict(finalized.diagnostics)
            if clean_raw_text != raw_text:
                diagnostics["incompleteControlMarkerSuppressed"] = True
            if key in self._sticker_diagnostics:
                diagnostics["stream"] = dict(self._sticker_diagnostics[key])
            self._sticker_diagnostics[key] = diagnostics
            self._sticker_final_by_turn[(session.session_id, session.current_turn_id)] = finalized.text
        return StickerResult(finalized.text, finalized.keys, diagnostics)

    def _record(
        self,
        session: AgentSession,
        kind: AgentEventKind,
        *,
        data: dict[str, object],
    ) -> AgentEvent:
        payload = dict(data)

        if kind is AgentEventKind.MODEL_RESPONSE:
            step_id = str(payload.get("step_id") or "").strip()
            raw_text = str(payload.get("text") or "")
            if step_id:
                finalized = self._finalize_sticker_model_text(
                    session,
                    step_id=step_id,
                    raw_text=raw_text,
                )
                payload["text"] = finalized.text
                payload["sticker_protocol"] = {
                    "schema": "inline_sticker_diagnostics_v4_structured_sidecar",
                    "keys": list(finalized.keys),
                    "output_marker_count": len(finalized.keys),
                }
                if session.messages:
                    last = session.messages[-1]
                    if last.role is MessageRole.ASSISTANT and isinstance(last.content, str):
                        session.messages[-1] = replace(last, content=finalized.text)

        elif kind is AgentEventKind.TURN_COMPLETED:
            with self._sticker_guard:
                final_text = self._sticker_final_by_turn.get(
                    (session.session_id, session.current_turn_id)
                )
            if final_text is not None:
                session.final_text = final_text
                payload["text"] = final_text

        event = super()._record(session, kind, data=payload)

        if kind is AgentEventKind.MODEL_REQUESTED:
            step_id = str(payload.get("step_id") or "").strip()
            context = _ModelStreamContext(
                session_id=session.session_id,
                turn_id=session.current_turn_id,
                step_id=step_id,
                profile_id=session.profile_id,
            )
            self._stream_context.set(context)
            if self.provider_streaming_enabled and step_id:
                with self._sticker_guard:
                    preferences = self._sticker_preferences
                sanitizer = StickerStreamSanitizer(
                    preferences,
                    self._sticker_context_for_session(session, streaming=True),
                )
                with self._sticker_guard:
                    self._sticker_streams[self._sticker_stream_key(context)] = sanitizer
                    if any(
                        message.role is MessageRole.SYSTEM
                        and str(getattr(message, "name", "") or "") == "loom_compaction"
                        for message in session.messages
                    ):
                        self._buffered_checkpoint_streams.add(self._sticker_stream_key(context))

        if kind is AgentEventKind.MODEL_RESPONSE:
            step_id = str(payload.get("step_id") or "").strip()
            if step_id:
                key = (session.session_id, session.current_turn_id, step_id)
                with self._sticker_guard:
                    self._sticker_streams.pop(key, None)
                    self._sticker_stream_text.pop(key, None)
                    self._buffered_checkpoint_streams.discard(key)
            self._stream_context.set(None)

        if kind in {
            AgentEventKind.TURN_COMPLETED,
            AgentEventKind.TURN_FAILED,
            AgentEventKind.TURN_CANCELLED,
            AgentEventKind.TURN_INTERRUPTED,
            AgentEventKind.LIMIT_REACHED,
        }:
            self._stream_context.set(None)
            with self._sticker_guard:
                turn_key = (session.session_id, session.current_turn_id)
                self._sticker_final_by_turn.pop(turn_key, None)
                stale = [
                    key
                    for key in self._sticker_streams
                    if key[0] == session.session_id and key[1] == session.current_turn_id
                ]
                for key in stale:
                    self._sticker_streams.pop(key, None)
                    self._sticker_stream_text.pop(key, None)
                    self._sticker_diagnostics.pop(key, None)
                    self._buffered_checkpoint_streams.discard(key)
        return event

    def close(self) -> None:
        with self._stream_listener_guard:
            self._stream_listeners.clear()
        with self._sticker_guard:
            self._sticker_streams.clear()
            self._sticker_stream_text.clear()
            self._sticker_diagnostics.clear()
            self._sticker_final_by_turn.clear()
            self._buffered_checkpoint_streams.clear()
        self._stream_context.set(None)
        super().close()


__all__ = [
    "AgentStreamEvent",
    "AgentStreamEventKind",
    "AgentStreamListener",
    "StreamingAgentRuntime",
]
