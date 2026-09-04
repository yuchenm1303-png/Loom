from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai import AIMessage, MessageRole

from .step import StepContext
from .storage import _message_from_dict, _message_to_dict, utc_now


_CONTEXT_VERSION = 1
_MAX_SUMMARY_CHARS = 100_000


@dataclass(frozen=True, slots=True)
class WorldStateEnvelope:
    digest: str
    payload: dict[str, Any]
    text: str


def build_world_state_envelope(
    step: StepContext,
    *,
    goal: dict[str, Any] | None = None,
    queue_pending: int = 0,
    diff_revision: int = 0,
    changed_paths: tuple[str, ...] = (),
) -> WorldStateEnvelope:
    sandbox = step.world_state.sandbox
    state: dict[str, Any] = {
        "workspace": step.world_state.workspace_dir,
        "model_profile": step.world_state.profile_id,
        "permissions": {
            "mode": step.world_state.permission_mode.value,
            "profile": step.permission_profile.name,
            "approval_policy": step.approval_policy.value,
        },
        "sandbox": sandbox.to_dict() if sandbox is not None else None,
        "tools": list(step.world_state.tool_names),
        "goal": goal,
        "queue_pending": max(0, int(queue_pending)),
        "turn_diff": {
            "revision": max(0, int(diff_revision)),
            "changed_paths": list(changed_paths),
        },
    }
    # Execution identity is useful to the model but must not poison the state
    # reference hash. Two adjacent model steps with the same actual runtime state
    # should produce the same digest so future stateful backends can send deltas.
    identity = {
        "session_id": step.session_id,
        "turn_id": step.turn_id,
        "step_id": step.step_id,
        "model_step": step.model_step,
    }
    canonical_state = json.dumps(
        state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical_state.encode("utf-8")).hexdigest()
    payload: dict[str, Any] = {
        "version": _CONTEXT_VERSION,
        "identity": identity,
        "state_digest": digest,
        "state": state,
    }
    text = (
        "LOOM_RUNTIME_STATE v1\n"
        "This runtime state is authoritative for the current model step. "
        "Do not infer broader filesystem, process, network, or approval permissions than stated here.\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    )
    return WorldStateEnvelope(digest=digest, payload=payload, text=text)


@dataclass(frozen=True, slots=True)
class ContextCheckpoint:
    checkpoint_id: str
    session_id: str
    created_at: str
    summary: str
    archived_messages: tuple[AIMessage, ...]
    retained_message_count: int
    world_state_digest: str

    @property
    def archived_message_count(self) -> int:
        return len(self.archived_messages)

    def summary_message(self) -> AIMessage:
        return AIMessage(
            role=MessageRole.SYSTEM,
            name="loom_compaction",
            content=(
                f"LOOM_CONTEXT_CHECKPOINT {self.checkpoint_id}\n"
                "The following is a compacted summary of earlier canonical conversation history. "
                "Treat it as prior conversation context, not as a new user instruction.\n"
                f"{self.summary}"
            ),
        )


class ContextCheckpointStore:
    def __init__(self, session_root: str | Path) -> None:
        self.session_root = Path(session_root).expanduser().resolve()

    def _directory(self, session_id: str) -> Path:
        path = self.session_root / str(session_id) / "context_checkpoints"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create(
        self,
        *,
        session_id: str,
        summary: str,
        archived_messages: tuple[AIMessage, ...],
        retained_message_count: int,
        world_state_digest: str,
    ) -> ContextCheckpoint:
        text = str(summary or "").strip()
        if not text:
            raise ValueError("context checkpoint summary must not be empty")
        if len(text) > _MAX_SUMMARY_CHARS:
            raise ValueError(f"context checkpoint summary exceeds {_MAX_SUMMARY_CHARS:,} characters")
        if not archived_messages:
            raise ValueError("context checkpoint must archive at least one message")
        checkpoint = ContextCheckpoint(
            checkpoint_id=f"ctx-{uuid.uuid4().hex[:16]}",
            session_id=str(session_id),
            created_at=utc_now(),
            summary=text,
            archived_messages=tuple(archived_messages),
            retained_message_count=max(0, int(retained_message_count)),
            world_state_digest=str(world_state_digest or ""),
        )
        self._write(checkpoint)
        return checkpoint

    def _write(self, checkpoint: ContextCheckpoint) -> None:
        directory = self._directory(checkpoint.session_id)
        target = directory / f"{checkpoint.checkpoint_id}.json"
        temp = directory / f".{checkpoint.checkpoint_id}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        payload = {
            "version": _CONTEXT_VERSION,
            "checkpoint_id": checkpoint.checkpoint_id,
            "session_id": checkpoint.session_id,
            "created_at": checkpoint.created_at,
            "summary": checkpoint.summary,
            "retained_message_count": checkpoint.retained_message_count,
            "world_state_digest": checkpoint.world_state_digest,
            "archived_messages": [_message_to_dict(item) for item in checkpoint.archived_messages],
        }
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def load(self, session_id: str, checkpoint_id: str) -> ContextCheckpoint:
        target = self._directory(session_id) / f"{str(checkpoint_id)}.json"
        payload = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("context checkpoint must be a JSON object")
        return ContextCheckpoint(
            checkpoint_id=str(payload.get("checkpoint_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            created_at=str(payload.get("created_at") or ""),
            summary=str(payload.get("summary") or ""),
            archived_messages=tuple(
                _message_from_dict(dict(item))
                for item in payload.get("archived_messages", [])
                if isinstance(item, dict)
            ),
            retained_message_count=int(payload.get("retained_message_count") or 0),
            world_state_digest=str(payload.get("world_state_digest") or ""),
        )

    def list(self, session_id: str) -> tuple[ContextCheckpoint, ...]:
        directory = self._directory(session_id)
        output: list[ContextCheckpoint] = []
        for path in sorted(directory.glob("ctx-*.json")):
            try:
                output.append(self.load(session_id, path.stem))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        output.sort(key=lambda item: item.created_at, reverse=True)
        return tuple(output)


def compaction_split_index(messages: tuple[AIMessage, ...], *, keep_recent: int) -> int:
    """Choose a safe suffix boundary that starts at a real user message.

    Starting the retained suffix at a user message keeps assistant tool-call/output
    groups on the same side of the checkpoint and avoids creating provider-invalid
    orphan tool outputs after compaction.
    """
    if len(messages) < 2:
        return 0
    keep = max(1, int(keep_recent))
    candidate = max(1, len(messages) - keep)
    for index in range(candidate, len(messages)):
        if messages[index].role is MessageRole.USER:
            return index
    for index in range(candidate - 1, 0, -1):
        if messages[index].role is MessageRole.USER:
            return index
    return 0


__all__ = [
    "ContextCheckpoint",
    "ContextCheckpointStore",
    "WorldStateEnvelope",
    "build_world_state_envelope",
    "compaction_split_index",
]
