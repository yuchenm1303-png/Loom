from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.ai import AIMessage, ImagePart, MessageRole, ModelUsage, TextPart, ToolCall

from .journal import atomic_bytes, atomic_json, atomic_text, session_lock, recover, repair_tail
from .event_cache import EventParseCache, _clone
from .session_overview import SessionOverviewCache

from .contracts import (
    AgentEvent,
    AgentEventKind,
    AgentSession,
    AgentStatus,
    ApprovalKind,
    PendingToolApproval,
    PermissionMode,
    ToolEffect,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _recent_event_lines(handle, limit: int) -> list[bytes]:
    """Scan backwards by blocks, retaining only complete selected records."""
    position = handle.seek(0, 2)
    remainder = b""
    selected: list[bytes] = []
    tail_has_newline = True
    first_block = True
    while position and len(selected) < limit:
        start = max(0, position - 8192)
        handle.seek(start)
        chunk = handle.read(position - start)
        if first_block:
            tail_has_newline = chunk.endswith(b"\n")
            first_block = False
        parts = (chunk + remainder).split(b"\n")
        remainder = parts[0]
        for line in reversed(parts[1:]):
            if not line.strip():
                continue
            ending = b"" if not selected and not tail_has_newline else b"\n"
            selected.append(line + ending)
            if len(selected) == limit:
                break
        position = start
    if not position and len(selected) < limit and remainder.strip():
        ending = b"" if not selected and not tail_has_newline else b"\n"
        selected.append(remainder + ending)
    return list(reversed(selected))


def _tool_call_to_dict(call: ToolCall) -> dict[str, Any]:
    return {"call_id": call.call_id, "name": call.name, "arguments": call.arguments}


def _tool_call_from_dict(payload: dict[str, Any]) -> ToolCall:
    return ToolCall(
        call_id=str(payload.get("call_id") or ""),
        name=str(payload.get("name") or ""),
        arguments=dict(payload.get("arguments") or {}),
    )


def _message_to_dict(message: AIMessage) -> dict[str, Any]:
    if isinstance(message.content, str):
        content: Any = message.content
    else:
        content = []
        for part in message.content:
            if isinstance(part, TextPart):
                content.append({"type": "text", "text": part.text})
            elif isinstance(part, ImagePart):
                content.append(
                    {"type": "image", "image_url": part.image_url, "detail": part.detail}
                )
            else:  # pragma: no cover - AI contracts reject unsupported parts
                raise TypeError("unsupported AI message part")
    return {
        "role": message.role.value,
        "content": content,
        "name": message.name,
        "tool_call_id": message.tool_call_id,
        "tool_calls": [_tool_call_to_dict(call) for call in message.tool_calls],
        # Persisted because a resumed session still has to replay this assistant
        # turn to a thinking-mode provider in the shape it requires back. Also
        # makes the estimator account for reasoning, which is really on the wire.
        "reasoning": message.reasoning,
        "phase": message.phase,
    }


def _message_from_dict(payload: dict[str, Any]) -> AIMessage:
    raw_content = payload.get("content", "")
    if isinstance(raw_content, list):
        parts = []
        for item in raw_content:
            if not isinstance(item, dict):
                raise ValueError("invalid persisted AI multipart content")
            kind = str(item.get("type") or "")
            if kind == "text":
                parts.append(TextPart(str(item.get("text") or "")))
            elif kind == "image":
                parts.append(
                    ImagePart(
                        str(item.get("image_url") or ""),
                        detail=str(item.get("detail") or "auto"),
                    )
                )
            else:
                raise ValueError(f"unsupported persisted AI content type: {kind!r}")
        content: Any = tuple(parts)
    else:
        content = str(raw_content or "")
    return AIMessage(
        role=MessageRole(str(payload.get("role") or "")),
        content=content,
        name=str(payload.get("name") or ""),
        tool_call_id=str(payload.get("tool_call_id") or ""),
        tool_calls=tuple(
            _tool_call_from_dict(dict(item))
            for item in payload.get("tool_calls", [])
            if isinstance(item, dict)
        ),
        reasoning=str(payload.get("reasoning") or ""),
        phase=payload.get("phase"),
    )


def _approval_to_dict(value: PendingToolApproval | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "call_id": value.call_id,
        "tool_name": value.tool_name,
        "arguments": value.arguments,
        "effect": value.effect.value,
        "reason": value.reason,
        "kind": value.kind.value,
        "retry_reason": value.retry_reason,
    }


def _approval_from_dict(value: Any) -> PendingToolApproval | None:
    if not isinstance(value, dict):
        return None
    return PendingToolApproval(
        call_id=str(value.get("call_id") or ""),
        tool_name=str(value.get("tool_name") or ""),
        arguments=dict(value.get("arguments") or {}),
        effect=ToolEffect(str(value.get("effect") or ToolEffect.READ_ONLY.value)),
        reason=str(value.get("reason") or ""),
        kind=ApprovalKind(str(value.get("kind") or ApprovalKind.INITIAL.value)),
        retry_reason=str(value.get("retry_reason") or ""),
    )


def _usage_to_dict(usage: ModelUsage) -> dict[str, int]:
    return {
        "input_tokens": int(usage.input_tokens),
        "output_tokens": int(usage.output_tokens),
        "total_tokens": int(usage.total_tokens),
        "cached_input_tokens": int(usage.cached_input_tokens),
    }


def _usage_from_dict(payload: Any) -> ModelUsage:
    data = payload if isinstance(payload, dict) else {}
    return ModelUsage(
        input_tokens=int(data.get("input_tokens") or 0),
        output_tokens=int(data.get("output_tokens") or 0),
        total_tokens=int(data.get("total_tokens") or 0),
        cached_input_tokens=int(data.get("cached_input_tokens") or 0),
    )


def session_to_dict(session: AgentSession) -> dict[str, Any]:
    return _session_document(session, [_message_to_dict(message) for message in session.messages],
                             session.request_context_frames)


def _session_document(session: AgentSession, messages: Any, frames: Any) -> dict[str, Any]:
    """The snapshot with ``messages`` and ``frames`` as given, so that the encoder can stand in for the two big lists."""
    return {
        "version": 2,
        "session_id": session.session_id,
        "profile_id": session.profile_id,
        "system_prompt": session.system_prompt,
        "system_prompt_version": session.system_prompt_version,
        "workspace_dir": session.workspace_dir,
        "permission_mode": session.permission_mode.value,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "status": session.status.value,
        "current_turn_id": session.current_turn_id,
        "forked_from_id": session.forked_from_id,
        "communication_language": session.communication_language,
        "model_selection": session.model_selection,
        "model": session.model,
        "model_provider": session.model_provider,
        "model_base_url": session.model_base_url,
        "model_vision": session.model_vision,
        "reasoning_kind": session.reasoning_kind,
        "reasoning_value": session.reasoning_value,
        "tool_selection": session.tool_selection,
        "messages": messages,
        "request_context_frames": frames,
        "pending_tool_calls": [_tool_call_to_dict(call) for call in session.pending_tool_calls],
        "pending_step_id": session.pending_step_id,
        "pending_bindings": session.pending_bindings,
        "steering_ids": session.steering_ids,
        "active_skills": session.active_skills,
        "pending_approval": _approval_to_dict(session.pending_approval),
        "model_steps": session.model_steps,
        "tool_calls": session.tool_calls,
        "usage": _usage_to_dict(session.usage),
        "final_text": session.final_text,
        "error": session.error,
    }


def _encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# Standing in for the two big lists while the rest of the snapshot is encoded. They are made once, so a message or a
# tool argument cannot contain them by accident; if one ever does, ``encode`` notices and encodes in one piece.
_MESSAGES_TOKEN = "loom-messages-" + uuid.uuid4().hex
_FRAMES_TOKEN = "loom-frames-" + uuid.uuid4().hex
_UPDATED_AT = re.compile(r'"updated_at":"[^"]*"')
# Sessions whose encoded messages are kept between commits.
_ENCODER_SESSIONS = 8
# The newest context frames are encoded again at every commit: the step that appended one adds its capture timings
# after the snapshot that first held it was written. Older frames are final.
_FRAMES_REENCODED = 3
# Likewise for the messages that carry tool calls: the arguments are the dict a tool handler is handed (see
# orchestrator), so a handler may edit them while the call runs. Once newer calls have been made, the call has finished.
_CALL_MESSAGES_REENCODED = 8


class _SnapshotEncoder:
    """Encodes the snapshot of one session, reusing what cannot have changed since the last time.

    A long conversation is megabytes of messages and every durable event commits a snapshot: five commits for each
    tool call, each encoding the whole of it (36 ms for a 3.7 MB session with the indented, key-sorted encoding the
    file used to have, which also keeps Python's JSON encoder out of its C fast path). The conversation only grows
    at its end, so the encodings of the messages and of the context frames that are still the ones of the last
    snapshot are reused, and only what was added is encoded. Two kinds of item are encoded again anyway:

    * the newest few messages that carry tool calls, because their arguments are a dict that a tool handler is handed
      and may edit in place while the call runs (see ``_CALL_MESSAGES_REENCODED``); every other message is frozen,
      so its encoding is a fact about it;
    * the newest few frames (see ``_FRAMES_REENCODED``).

    The rest of the document is small and encoded each time.
    """

    def __init__(self) -> None:
        # What the previous snapshot was made of, and the encodings of those items, in order.
        self._messages: list[AIMessage] = []
        self._message_parts: list[bytes] = []
        self._call_positions: list[int] = []
        self._frames: list[dict[str, Any]] = []
        self._frame_parts: list[bytes] = []
        # What the snapshot file holds, as far as this process wrote it: what it was made of, and the size and
        # modification time the file had afterwards (so a file somebody else touched is never taken for ours).
        self.persisted: object = None
        self.persisted_file: tuple[int, int] | None = None

    def _encode_messages(self, messages: list[AIMessage]) -> list[bytes]:
        known = self._messages
        reuse = len(known)
        # List comparison takes the identity shortcut for every message that is still the same object, which is
        # nearly all of them; a message replaced by an equal one gives the same encoding, so it passes as well.
        if reuse and (len(messages) < reuse or messages[:reuse] != known):
            reuse = 0
        if reuse:
            parts = self._message_parts[:reuse]
            calls = self._call_positions
            for index in calls[-_CALL_MESSAGES_REENCODED:]:
                parts[index] = _encode(_message_to_dict(messages[index]))
        else:
            parts, calls = [], []
        for index in range(reuse, len(messages)):
            message = messages[index]
            parts.append(_encode(_message_to_dict(message)))
            if message.tool_calls:
                calls.append(index)
        self._messages, self._message_parts, self._call_positions = messages, parts, calls
        return parts

    def _encode_frames(self, frames: list[dict[str, Any]]) -> list[bytes]:
        known = self._frames
        reuse = len(known)
        if reuse and (len(frames) < reuse or frames[:reuse] != known):
            reuse = 0
        kept = max(0, reuse - _FRAMES_REENCODED)
        parts = self._frame_parts[:kept]
        for index in range(kept, len(frames)):
            parts.append(_encode(frames[index]))
        self._frames, self._frame_parts = frames, parts
        return parts

    def encode(self, session: AgentSession) -> tuple[bytes, object]:
        """The snapshot, and a key that two snapshots share exactly when they say the same thing.

        ``updated_at`` is left out of the key: it moves with every commit and says nothing about the state.
        """
        messages = list(session.messages)
        frames = list(session.request_context_frames)
        message_parts = self._encode_messages(messages)
        frame_parts = self._encode_frames(frames)
        text = json.dumps(_session_document(session, _MESSAGES_TOKEN, _FRAMES_TOKEN), ensure_ascii=False, separators=(",", ":"))
        messages_needle, frames_needle = '"' + _MESSAGES_TOKEN + '"', '"' + _FRAMES_TOKEN + '"'
        if text.count(messages_needle) != 1 or text.count(frames_needle) != 1 or text.find(messages_needle) > text.find(frames_needle):
            # A field of the document says the placeholder (or the fields are not in the order assumed): encode it plainly.
            data = _encode(_session_document(session, [_message_to_dict(message) for message in messages], frames))
            return data, ("whole", _UPDATED_AT.sub('"updated_at":""', data.decode("utf-8"), count=1))
        head, _, rest = text.partition(messages_needle)
        middle, _, tail = rest.partition(frames_needle)
        data = b"".join((head.encode("utf-8"), b"[", b",".join(message_parts), b"]", middle.encode("utf-8"),
                         b"[", b",".join(frame_parts), b"]", tail.encode("utf-8")))
        return data, (_UPDATED_AT.sub('"updated_at":""', head, count=1), middle, tail, message_parts, frame_parts)


def session_from_dict(payload: dict[str, Any]) -> AgentSession:
    return AgentSession(
        session_id=str(payload.get("session_id") or ""),
        profile_id=str(payload.get("profile_id") or ""),
        system_prompt=str(payload.get("system_prompt") or ""),
        system_prompt_version=int(payload.get("system_prompt_version") or 0),
        workspace_dir=str(payload.get("workspace_dir") or ""),
        permission_mode=PermissionMode(
            str(payload.get("permission_mode") or PermissionMode.APPROVAL.value)
        ),
        created_at=str(payload.get("created_at") or ""),
        updated_at=str(payload.get("updated_at") or ""),
        status=AgentStatus(str(payload.get("status") or AgentStatus.IDLE.value)),
        current_turn_id=str(payload.get("current_turn_id") or ""),
        forked_from_id=str(payload.get("forked_from_id") or ""),
        communication_language=str(payload.get("communication_language") or "auto"),
        model_selection=str(payload.get("model_selection") or ""),
        model=str(payload.get("model") or ""),
        model_provider=str(payload.get("model_provider") or ""),
        model_base_url=str(payload.get("model_base_url") or ""),
        model_vision=payload.get("model_vision") if isinstance(payload.get("model_vision"), bool) else None,
        reasoning_kind=str(payload.get("reasoning_kind") or ""),
        reasoning_value=str(payload.get("reasoning_value") or ""),
        tool_selection=payload.get("tool_selection") if isinstance(payload.get("tool_selection"), list) else None,
        messages=[
            _message_from_dict(dict(item))
            for item in payload.get("messages", [])
            if isinstance(item, dict)
        ],
        pending_tool_calls=[
            _tool_call_from_dict(dict(item))
            for item in payload.get("pending_tool_calls", [])
            if isinstance(item, dict)
        ],
        pending_step_id=str(payload.get("pending_step_id") or ""),
        pending_bindings=dict(payload.get("pending_bindings") or {}),
        steering_ids=list(payload.get("steering_ids") or []),
        active_skills=dict(payload.get("active_skills") or {}),
        request_context_frames=list(payload.get("request_context_frames") or []),
        pending_approval=_approval_from_dict(payload.get("pending_approval")),
        model_steps=int(payload.get("model_steps") or 0),
        tool_calls=int(payload.get("tool_calls") or 0),
        usage=_usage_from_dict(payload.get("usage")),
        final_text=str(payload.get("final_text") or ""),
        error=str(payload.get("error") or ""),
    )


def _context_event_projection(payload):
    """Execution context needs milestones/lifecycle, not large observation bodies.

    Raw events stay durable and are read through events(). This projection keeps
    chronology and IDs so it can share the append/truncate-safe parse cache.
    """
    data = payload.get("data") or {}
    kind = payload.get("kind")
    if kind in {"plan_updated", "browser_session_opened", "browser_session_released"}:
        selected = dict(data)
    elif kind in {"verification_updated", "verification_finish_requested"}:
        selected = dict(data)
    elif kind == "check_recorded":
        selected = {key: data[key] for key in ("case_id", "expectation", "verdict", "assessment_source",
                                             "subject_snapshot", "acceptance_evidence", "check_kind", "evidence_refs",
                                             "definition_fingerprint", "definition_changed") if key in data}
    elif kind == "tool_started":
        selected = {key: data[key] for key in ("call_id", "tool", "effect", "call_fingerprint", "repeat_count") if key in data}
    elif kind == "tool_failed":
        result = data.get("data") or {}
        selected = {"data": {key: result[key] for key in ("invalid_request_fingerprint", "repeat_count") if key in result}}
    elif kind in {"model_requested", "model_response", "model_response_rejected"}:
        selected = {key: data[key] for key in ("profile_id", "provider", "model", "usage", "reason",
                    "estimated_input_tokens_before", "estimated_input_tokens_after", "rejected_input_tokens") if key in data}
    else:
        selected = {}
    if "browser_resources" in data:
        selected["browser_resources"] = data["browser_resources"]
    return {**payload, "data": selected}


# Records are written with ``data`` last, so what a line is can be read from its
# first few hundred bytes. Views that only need some records use that to avoid
# parsing the rest: on a long session most of the log is request payloads.
_DATA_KEY = b',"data":'
_KIND_FIELD = re.compile(rb'"kind":"([^"\\]*)"')
_HEADER_FIELDS = frozenset(("event_id", "session_id", "turn_id", "kind", "created_at"))
_CONTEXT_STATE_KINDS = frozenset(("model_requested", "context_checkpointed"))


def _header_kind(line):
    """The kind of a record, or None when the line does not have the usual shape."""
    end = line.find(_DATA_KEY, 0, 512)
    if end < 0:
        return None
    found = _KIND_FIELD.search(line, 0, end)
    return found.group(1) if found else None


def _header(line):
    """The fields in front of ``data``, parsed without touching the body."""
    end = line.find(_DATA_KEY, 0, 512)
    if end < 0:
        return None
    try:
        head = json.loads(line[:end] + b"}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return head if isinstance(head, dict) and _HEADER_FIELDS <= head.keys() else None


def _presentation_line(line):
    """A record without the body of a model request, which no transcript reads.

    A ``model_requested`` payload repeats the request layout, which grows with the
    conversation and is most of a long session's log. The record itself stays, so
    turns, ordering and timing are unchanged.
    """
    if _header_kind(line) == b"model_requested":
        head = _header(line)
        if head is not None and head["kind"] == "model_requested":
            head["data"] = {}
            return head
    return json.loads(line)


def _checkpoint_line(line):
    """Compaction checkpoints only, so counting them does not parse the whole log."""
    kind = _header_kind(line)
    if kind is not None and kind != b"context_checkpointed":
        return None
    payload = json.loads(line)
    return payload if payload.get("kind") == "context_checkpointed" else None


def _newest_payload(path, kinds):
    """The newest record whose kind is in ``kinds``, found by reading from the end."""
    if not path.is_file():
        return None
    wanted = {kind.encode() for kind in kinds}
    with path.open("rb") as handle:
        size = handle.seek(0, 2)
        span = 1 << 16
        while True:
            start = max(0, size - span)
            handle.seek(start)
            lines = handle.read(size - start).split(b"\n")
            if start:
                lines = lines[1:]  # begins inside a record
            tail = lines.pop() if lines else b""
            if tail.strip():
                # An unterminated last line only counts once it is a whole record.
                try:
                    payload = json.loads(tail)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    payload = None
                if isinstance(payload, dict) and payload.get("kind") in kinds:
                    return payload
            for line in reversed(lines):
                if not line.strip():
                    continue
                kind = _header_kind(line)
                if kind is not None and kind not in wanted:
                    continue
                payload = json.loads(line)
                if payload.get("kind") in kinds:
                    return payload
            if not start:
                return None
            span *= 8


def _event_from_payload(payload):
    return AgentEvent(
        event_id=str(payload.get("event_id") or ""),
        session_id=str(payload.get("session_id") or ""),
        turn_id=str(payload.get("turn_id") or ""),
        kind=AgentEventKind(str(payload.get("kind") or "")),
        created_at=str(payload.get("created_at") or ""),
        data=dict(payload.get("data") or {}),
    )


def _build_event(record, shared):
    """An AgentEvent from a cached record. Only the body can be mutated through it, so only it is copied."""
    body = record.get("data") or {}
    return AgentEvent(
        event_id=str(record.get("event_id") or ""),
        session_id=str(record.get("session_id") or ""),
        turn_id=str(record.get("turn_id") or ""),
        kind=AgentEventKind(str(record.get("kind") or "")),
        created_at=str(record.get("created_at") or ""),
        data=_clone(body) if shared and type(body) is dict else dict(body),
    )


class FileAgentSessionStore:
    """Local durable state for Loom Agent Runtime.

    ``session.json`` is an atomic resumable snapshot. ``events.jsonl`` is an
    append-only UI/audit feed. Observable state is persisted; private model
    chain-of-thought is not.
    """

    def __init__(self, runtime_root: str | Path) -> None:
        self.root = Path(runtime_root).expanduser().resolve() / "agent_runtime" / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self._event_cache = EventParseCache()
        self._overview_cache = SessionOverviewCache()
        self._encoders: OrderedDict[str, _SnapshotEncoder] = OrderedDict()
        self._encoders_guard = threading.Lock()

    def session_dir(self, session_id: str) -> Path:
        value = str(session_id or "").strip()
        if not value or any(char not in "0123456789abcdef-" for char in value.casefold()):
            raise ValueError("invalid agent session id")
        return self.root / value

    def workspace_dir(self, session_id: str) -> Path:
        path = self.session_dir(session_id) / "workspace"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create(self, session: AgentSession) -> None:
        directory = self.session_dir(session.session_id)
        if directory.exists():
            raise FileExistsError(f"agent session already exists: {session.session_id}")
        directory.mkdir(parents=True, exist_ok=False)
        internal_workspace = (directory / "workspace").resolve()
        if Path(session.workspace_dir).resolve() == internal_workspace:
            internal_workspace.mkdir(parents=True, exist_ok=True)
        self.save(session)

    def load_overview(self, session_id: str) -> SimpleNamespace:
        """Sidebar metadata only; callers must use load() to execute/resume."""
        directory = self.session_dir(session_id)
        with session_lock(directory):
            recover(directory)
            payload = self._overview_cache.read(directory / "session.json")
        # This intentionally is not an executable AgentSession: no prompt,
        # model context, approvals or tool bindings can escape the list path.
        text_fields = ("session_id", "profile_id", "workspace_dir", "created_at", "updated_at",
                       "current_turn_id", "forked_from_id", "model_selection", "model",
                       "model_provider", "model_base_url", "reasoning_kind", "reasoning_value")
        fields = {key: str(payload.get(key) or "").strip() for key in text_fields}
        fields["profile_id"] = fields["profile_id"].casefold()
        fields["model_provider"] = fields["model_provider"].casefold()
        fields["model_base_url"] = fields["model_base_url"].rstrip("/")
        if not all(fields[key] for key in ("session_id", "profile_id", "workspace_dir")):
            raise ValueError("session overview requires identity, profile and workspace")
        return SimpleNamespace(**fields,
            permission_mode=PermissionMode(payload.get("permission_mode") or PermissionMode.APPROVAL.value),
            status=AgentStatus(payload.get("status") or AgentStatus.IDLE.value),
            model_vision=payload.get("model_vision") if isinstance(payload.get("model_vision"), bool) else None,
            usage=_usage_from_dict(payload.get("usage")),
            messages=[_message_from_dict(message) for message in payload.get("messages", [])])

    def save(self, session: AgentSession) -> None:
        directory = self.session_dir(session.session_id)
        with session_lock(directory):
            recover(directory)
            self._save(session)

    def _save(self, session: AgentSession) -> None:
        session.updated_at = utc_now()
        encoder, data, key = self._snapshot(session)
        if self._persisted(encoder, key, session.session_id):
            return
        self._write_session(session, data)
        self._remember(encoder, key, session.session_id)

    def _snapshot(self, session: AgentSession) -> tuple[_SnapshotEncoder, bytes, object]:
        """The snapshot, encoded. Callers hold the session's commit lock, which is what serializes its encoder."""
        with self._encoders_guard:
            encoder = self._encoders.pop(session.session_id, None) or _SnapshotEncoder()
            self._encoders[session.session_id] = encoder
            while len(self._encoders) > _ENCODER_SESSIONS:
                self._encoders.popitem(last=False)
        data, key = encoder.encode(session)
        return encoder, data, key

    def _snapshot_file(self, session_id: str) -> tuple[int, int] | None:
        try:
            stat = (self.session_dir(session_id) / "session.json").stat()
        except OSError:
            return None
        return stat.st_size, stat.st_mtime_ns

    def _persisted(self, encoder: _SnapshotEncoder, key: object, session_id: str) -> bool:
        """Whether session.json already says what this snapshot says, so that writing it again would add nothing.

        Most events of a tool call (it was requested, it started, a process printed) change nothing a snapshot
        holds, and rewriting megabytes for each of them was most of what a long session spent per step. The file
        has to be exactly the one this process wrote last: a file that was replaced or recovered is written again.
        """
        return (encoder.persisted is not None and encoder.persisted == key
                and encoder.persisted_file is not None and encoder.persisted_file == self._snapshot_file(session_id))

    def _remember(self, encoder: _SnapshotEncoder, key: object, session_id: str) -> None:
        encoder.persisted, encoder.persisted_file = key, self._snapshot_file(session_id)

    def _write_session(self, session: AgentSession, data: bytes | str) -> None:
        directory = self.session_dir(session.session_id)
        directory.mkdir(parents=True, exist_ok=True)
        # Share the same fsynced, atomic Windows-safe path as redo recovery.
        (atomic_bytes if isinstance(data, bytes) else atomic_text)(directory / "session.json", data)

    def load(self, session_id: str) -> AgentSession:
        target = self.session_dir(session_id) / "session.json"
        with session_lock(target.parent):
            recover(target.parent)
            payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("agent session snapshot must be a JSON object")
        return session_from_dict(payload)

    def require_session(self, session_id: str) -> None:
        """Raise FileNotFoundError unless the session exists, without parsing it.

        Goal and queue lookups only need to know the thread is real. Parsing a
        long session.json for that cost tens of milliseconds, twice per model step.
        """
        directory = self.session_dir(session_id)
        with session_lock(directory):
            recover(directory)
            if not (directory / "session.json").is_file():
                raise FileNotFoundError(str(directory / "session.json"))

    def submit_steering(self, session_id: str, turn_id: str, text: str) -> None:
        directory = self.session_dir(session_id)
        with session_lock(directory):
            path = directory / "steering.json"
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            if len(items) >= 100 or len(text) > 100_000:
                raise ValueError("steering inbox limit reached")
            items.append({"id": uuid.uuid4().hex, "turn_id": turn_id, "text": text})
            atomic_json(path, items)

    def pending_steering(self, session_id: str, turn_id: str):
        directory = self.session_dir(session_id)
        with session_lock(directory):
            path = directory / "steering.json"
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return [item for item in items if item["turn_id"] == turn_id]

    def ack_steering(self, session_id: str, identifiers):
        directory = self.session_dir(session_id)
        with session_lock(directory):
            path = directory / "steering.json"
            items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            atomic_json(path, [item for item in items if item["id"] not in identifiers])

    def commit_event(self, session: AgentSession, event: AgentEvent) -> None:
        directory = self.session_dir(session.session_id)
        with session_lock(directory):
            recover(directory)
            session.updated_at = utc_now()
            payload = {"event_id": event.event_id, "session_id": event.session_id,
                "turn_id": event.turn_id, "kind": event.kind.value, "created_at": event.created_at, "data": event.data}
            # Encode the snapshot once and keep that encoding in the journal: a long
            # session is megabytes, and it used to be encoded twice for every event.
            encoder, snapshot, key = self._snapshot(session)
            if self._persisted(encoder, key, session.session_id):
                # The event changed nothing the snapshot holds: appending it is the whole commit.
                self._append_event(event)
                return
            atomic_bytes(directory / ".pending-commit.json",
                         b"".join((b'{"session":', snapshot, b',"event":', _encode(payload), b"}")))
            self._append_event(event)
            self._write_session(session, snapshot)
            (directory / ".pending-commit.json").unlink()
            self._remember(encoder, key, session.session_id)

    def append_event(self, event: AgentEvent) -> None:
        directory = self.session_dir(event.session_id)
        with session_lock(directory):
            recover(directory)
            self._append_event(event)

    def _append_event(self, event: AgentEvent) -> None:
        directory = self.session_dir(event.session_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "events.jsonl"
        payload = {
            "event_id": event.event_id,
            "session_id": event.session_id,
            "turn_id": event.turn_id,
            "kind": event.kind.value,
            "created_at": event.created_at,
            "data": event.data,
        }
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        repair_tail(path)
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def recent_events(self, session_id: str, limit: int = 256) -> tuple[AgentEvent, ...]:
        """Read a bounded tail without loading older transcript records."""
        return self.events(session_id, limit=limit)

    def context_events(self, session_id: str) -> tuple[AgentEvent, ...]:
        """Lightweight state/accounting projection; never inspect tool bodies here."""
        return self.events(session_id, context_only=True)

    def events(self, session_id: str, *, limit: int | None = None, context_only: bool = False) -> tuple[AgentEvent, ...]:
        if limit is not None and limit < 0:
            raise ValueError("event limit must not be negative")
        if limit == 0:
            return ()
        return self._view(session_id, limit=limit, project=_context_event_projection if context_only else None)

    def context_select(self, session_id: str, *, turn_id: str | None = None, kinds=None,
                       last: int | None = None) -> tuple[AgentEvent, ...]:
        """The context projection narrowed to what a caller reads, before any event of the rest is made.

        ``turn_id`` keeps the events of one turn, ``kinds`` those of some kinds (kinds or their values), and
        ``last`` the newest few of what remains. The result is what the same filters give on
        ``context_events()``, in the same order; on a long log it costs the events kept, not the events there are.
        """
        wanted = None if kinds is None else frozenset(getattr(kind, "value", kind) for kind in kinds)
        count = None if last is None else int(last)
        if count is not None and count <= 0:
            return ()

        def select(records):
            if turn_id is not None:
                records = [record for record in records if str(record.get("turn_id") or "") == turn_id]
            if wanted is not None:
                records = [record for record in records if record.get("kind") in wanted]
            return records[-count:] if count is not None else records

        return self._view(session_id, project=_context_event_projection, select=select)

    def last_event(self, session_id: str, kinds) -> AgentEvent | None:
        """The newest event of one of ``kinds``, found by reading the log from its end.

        Asking whether a session ever finished a turn must not cost a parse of
        everything it did.
        """
        wanted = frozenset(getattr(kind, "value", kind) for kind in kinds)
        path = self.session_dir(session_id) / "events.jsonl"
        with session_lock(path.parent):
            recover(path.parent)
            payload = _newest_payload(path, wanted)
        return _event_from_payload(payload) if payload is not None else None

    def presentation_events(self, session_id: str) -> tuple[AgentEvent, ...]:
        """Every event a transcript is built from, without the request payloads it never reads."""
        return self._view(session_id, parse=_presentation_line)

    def checkpoint_events(self, session_id: str) -> tuple[AgentEvent, ...]:
        """The context checkpoints so far, in order, at the cost of a view that holds only them."""
        return self._view(session_id, parse=_checkpoint_line)

    def context_state(self, session_id: str) -> tuple[tuple[AgentEvent, ...], AgentEvent | None]:
        """Compaction checkpoints so far and the newest model request or checkpoint.

        That is what the active context is made of, from one consistent read
        that parses neither the older requests nor the tool output between them.
        """
        path = self.session_dir(session_id) / "events.jsonl"
        with session_lock(path.parent):
            recover(path.parent)
            checkpoints = self._event_cache.read(path, None, _recent_event_lines, parse=_checkpoint_line,
                                                 build=_build_event)
            newest = _newest_payload(path, _CONTEXT_STATE_KINDS)
        return (
            tuple(checkpoints),
            _event_from_payload(newest) if newest is not None else None,
        )

    def _view(self, session_id: str, *, limit: int | None = None, project=None, parse=None, select=None) -> tuple[AgentEvent, ...]:
        path = self.session_dir(session_id) / "events.jsonl"
        with session_lock(path.parent):
            recover(path.parent)
            events = self._event_cache.read(path, limit, _recent_event_lines, project=project, parse=parse,
                                            build=_build_event, select=select)
        return tuple(events)


__all__ = [
    "FileAgentSessionStore",
    "session_from_dict",
    "session_to_dict",
    "utc_now",
]
