from __future__ import annotations

import json
import os
import threading
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
    build_sticker_system_prompt,
    finalize_reply,
    reconcile_stream_reply,
)
from .storage import utc_now


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

        self._sticker_guard = threading.RLock()
        self._sticker_streams: dict[tuple[str, str, str], StickerStreamSanitizer] = {}
        self._sticker_stream_text: dict[tuple[str, str, str], str] = {}
        self._sticker_diagnostics: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._sticker_final_by_turn: dict[tuple[str, str], str] = {}
        self._sticker_preferences = StickerPreferences.from_env()

        super().__init__(*args, **kwargs)
        self._sticker_preferences = self._load_sticker_preferences()

        enable = getattr(self.platform, "enable_streaming", None)
        subscribe = getattr(self.platform, "subscribe_stream", None)
        if callable(enable) and callable(subscribe):
            enable()
            subscribe(self._on_provider_stream)
            self._provider_streaming_enabled = True

    @property
    def provider_streaming_enabled(self) -> bool:
        return self._provider_streaming_enabled

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
        return StickerContext(
            user_text=user_text,
            assistant_history=tuple(assistant_history[-8:]),
            custom_instructions=custom_instructions,
            streaming=streaming,
            allow_stickers=True,
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
            with self._sticker_guard:
                sanitizer = self._sticker_streams.get(key)
            if sanitizer is None:
                # Preserve the old streaming path if sticker state was not
                # established for an unexpected detached/legacy execution.
                super()._on_provider_stream(event)
                return
            delta = sanitizer.push(event.text_delta)
            if delta:
                super()._on_provider_stream(replace(event, text_delta=delta))
            return

        if event.kind is ProviderStreamEventKind.COMPLETED:
            with self._sticker_guard:
                sanitizer = self._sticker_streams.get(key)
            if sanitizer is not None:
                tail = sanitizer.finish()
                if tail:
                    super()._on_provider_stream(
                        replace(
                            event,
                            kind=ProviderStreamEventKind.TEXT_DELTA,
                            text_delta=tail,
                        )
                    )
                with self._sticker_guard:
                    self._sticker_stream_text[key] = sanitizer.value()
                    self._sticker_diagnostics[key] = sanitizer.diagnostics()
            super()._on_provider_stream(event)
            return

        super()._on_provider_stream(event)

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
        provider_result = finalize_reply(raw_text, preferences, context)
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
            context = self._stream_context.get()
            if (
                self.provider_streaming_enabled
                and step_id
                and isinstance(context, _ModelStreamContext)
            ):
                with self._sticker_guard:
                    preferences = self._sticker_preferences
                sanitizer = StickerStreamSanitizer(
                    preferences,
                    self._sticker_context_for_session(session, streaming=True),
                )
                with self._sticker_guard:
                    self._sticker_streams[self._sticker_stream_key(context)] = sanitizer

        if kind is AgentEventKind.MODEL_RESPONSE:
            step_id = str(payload.get("step_id") or "").strip()
            if step_id:
                key = (session.session_id, session.current_turn_id, step_id)
                with self._sticker_guard:
                    self._sticker_streams.pop(key, None)
                    self._sticker_stream_text.pop(key, None)

        if kind in {
            AgentEventKind.TURN_COMPLETED,
            AgentEventKind.TURN_FAILED,
            AgentEventKind.TURN_CANCELLED,
            AgentEventKind.TURN_INTERRUPTED,
            AgentEventKind.LIMIT_REACHED,
        }:
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
        return event

    def close(self) -> None:
        with self._stream_listener_guard:
            self._stream_listeners.clear()
        with self._sticker_guard:
            self._sticker_streams.clear()
            self._sticker_stream_text.clear()
            self._sticker_diagnostics.clear()
            self._sticker_final_by_turn.clear()
        self._stream_context.set(None)
        super().close()


__all__ = [
    "AgentStreamEvent",
    "AgentStreamEventKind",
    "AgentStreamListener",
    "StreamingAgentRuntime",
]
