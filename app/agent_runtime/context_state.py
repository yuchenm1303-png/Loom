from __future__ import annotations

import hashlib
import json
import os
import platform
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.ai import AIMessage, MessageRole

from .step import StepContext
from .storage import _message_from_dict, _message_to_dict, utc_now


_CONTEXT_VERSION = 1
_MAX_SUMMARY_CHARS = 100_000

# One copy, because a second one drifted: MultiAgentRuntime re-rendered the
# envelope with its own edited wording, so changes here silently had no effect.
RUNTIME_STATE_PREAMBLE = (
    "LOOM_RUNTIME_STATE v1\n"
    "This runtime state is authoritative for the current model step. "
    "`environment` describes the machine you are running on; you may inspect it with "
    "your tools. "
    "Do not infer broader filesystem, process, network, approval, or sub-agent "
    "permissions than stated here.\n"
)


def render_runtime_state_text(payload: dict[str, Any]) -> str:
    return RUNTIME_STATE_PREAMBLE + json.dumps(
        payload, ensure_ascii=False, sort_keys=True, indent=2
    )


@dataclass(frozen=True, slots=True)
class WorldStateEnvelope:
    digest: str
    payload: dict[str, Any]
    text: str


def _default_shell() -> str:
    """The shell binary present on this host, if one can be named."""
    if os.name == "nt":
        return os.environ.get("COMSPEC") or "powershell.exe"
    return os.environ.get("SHELL") or "/bin/sh"


def build_host_environment() -> dict[str, Any]:
    """Describe the machine the model is actually running on.

    The envelope's preamble tells the model not to infer capabilities beyond
    what is stated. Stating a workspace and a permission mode but never a host
    therefore reads as "there is no host": a question about the computer falls
    outside the described world, and the model looks for an in-scope tool whose
    *name* matches instead of running a command.

    ``exec`` runs argv without an implicit shell, so naming the shell alone
    would invite the opposite error. The invocation note keeps the two facts
    together.
    """
    system = platform.system() or os.name
    friendly = {"Darwin": "macOS", "Windows": "Windows", "Linux": "Linux"}.get(system, system)
    shell = _default_shell()
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    return {
        "platform": friendly,
        "os_version": platform.release(),
        "shell": shell,
        "shell_invocation": (
            "exec runs argv directly with no implicit shell. To use the shell, "
            f"name it in argv, for example {_shell_example(shell)}."
        ),
        "current_date": now.date().isoformat(),
        # A localized tzname is encoded in the host code page and differs per
        # machine language; the offset says the same thing unambiguously.
        "utc_offset": f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset,
    }


def _shell_example(shell: str) -> str:
    stem = Path(shell).stem.casefold()
    if stem in {"powershell", "pwsh"}:
        return '["powershell", "-NoProfile", "-Command", "<command>"]'
    if stem == "cmd":
        return '["cmd", "/c", "<command>"]'
    return f'["{shell}", "-lc", "<command>"]'


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
        "environment": build_host_environment(),
        "workspace": step.world_state.workspace_dir,
        "model_profile": step.world_state.profile_id,
        "permissions": {
            "mode": step.world_state.permission_mode.value,
            "profile": step.permission_profile.name,
            "approval_policy": step.approval_policy.value,
        },
        "sandbox": sandbox.to_dict() if sandbox is not None else None,
        # Tool names are deliberately absent. Every callable tool is already
        # sent as its own schema with its own description; repeating ~50 bare
        # names here cost tokens and invited routing by name. It also listed
        # deferred and hidden tools that the model cannot call.
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
    return WorldStateEnvelope(
        digest=digest, payload=payload, text=render_runtime_state_text(payload)
    )


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
                "Treat it as prior conversation context, not as a new user instruction. "
                "Its language is historical content, not a response-language instruction; for all user-facing "
                "output follow the current LOOM_COMMUNICATION_LANGUAGE system message.\n"
                f"{self.summary}"
            ),
        )


class ContextCheckpointStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.checkpoints_root = self.root / "context_checkpoints"
        self.checkpoints_root.mkdir(parents=True, exist_ok=True)

    def _session_dir(self, session_id: str) -> Path:
        directory = self.checkpoints_root / session_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory

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
            raise ValueError("context summary must not be empty")
        if len(text) > _MAX_SUMMARY_CHARS:
            raise ValueError("context summary is too large")
        checkpoint = ContextCheckpoint(
            checkpoint_id=str(uuid.uuid4()),
            session_id=str(session_id),
            created_at=utc_now(),
            summary=text,
            archived_messages=tuple(archived_messages),
            retained_message_count=max(0, int(retained_message_count)),
            world_state_digest=str(world_state_digest or ""),
        )
        path = self._session_dir(checkpoint.session_id) / f"{checkpoint.checkpoint_id}.json"
        payload = {
            "checkpoint_id": checkpoint.checkpoint_id,
            "session_id": checkpoint.session_id,
            "created_at": checkpoint.created_at,
            "summary": checkpoint.summary,
            "archived_messages": [_message_to_dict(message) for message in checkpoint.archived_messages],
            "retained_message_count": checkpoint.retained_message_count,
            "world_state_digest": checkpoint.world_state_digest,
        }
        temp = path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
        return checkpoint

    def load(self, session_id: str, checkpoint_id: str) -> ContextCheckpoint:
        path = self._session_dir(session_id) / f"{checkpoint_id}.json"
        if not path.is_file():
            raise KeyError(f"unknown context checkpoint: {checkpoint_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ContextCheckpoint(
            checkpoint_id=str(payload["checkpoint_id"]),
            session_id=str(payload["session_id"]),
            created_at=str(payload["created_at"]),
            summary=str(payload["summary"]),
            archived_messages=tuple(_message_from_dict(item) for item in payload.get("archived_messages", [])),
            retained_message_count=int(payload.get("retained_message_count", 0)),
            world_state_digest=str(payload.get("world_state_digest", "")),
        )

    def list(self, session_id: str) -> tuple[ContextCheckpoint, ...]:
        directory = self._session_dir(session_id)
        checkpoints: list[ContextCheckpoint] = []
        for path in sorted(directory.glob("*.json")):
            try:
                checkpoints.append(self.load(session_id, path.stem))
            except Exception:
                continue
        checkpoints.sort(key=lambda item: item.created_at, reverse=True)
        return tuple(checkpoints)
