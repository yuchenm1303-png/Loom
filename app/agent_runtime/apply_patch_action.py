from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ai import ToolCall

from .patch_format import parse_text_patch
from .patch_runtime import ApplyPatchRuntime, PatchPlan
from .tools import ToolContext


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _content_digest(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _planning_context(step) -> ToolContext:
    return ToolContext(
        session_id=step.session_id,
        turn_id=step.turn_id,
        workspace=Path(step.world_state.workspace_dir),
        permission_mode=step.permissions.mode.value,
    )


def _plan_for_call(step, call: ToolCall) -> PatchPlan:
    if str(call.name or "") != "apply_patch":
        raise ValueError("ApplyPatchActionIdentity requires an apply_patch tool call")
    arguments = dict(call.arguments)
    context = _planning_context(step)
    try:
        if "patch" in arguments:
            raw_changes = parse_text_patch(context, arguments["patch"])
        elif "changes" in arguments:
            raw_changes = arguments["changes"]
        else:
            raise ValueError("apply_patch requires patch or changes")
        return ApplyPatchRuntime().plan(context, raw_changes)
    except OSError as exc:
        raise ValueError(f"apply_patch planning failed: {exc}") from exc


@dataclass(frozen=True, slots=True)
class PlannedFileChangeIdentity:
    path: str
    before_digest: str | None
    after_digest: str | None

    def binding_payload(self) -> dict[str, object]:
        return {
            "path": self.path,
            "before_digest": self.before_digest,
            "after_digest": self.after_digest,
        }


@dataclass(frozen=True, slots=True)
class ApplyPatchActionIdentity:
    """Canonical identity for the effective file changes of one apply_patch call."""

    call_id: str
    changes: tuple[PlannedFileChangeIdentity, ...]

    @property
    def kind(self) -> str:
        return "apply_patch"

    @classmethod
    def build(cls, step, call: ToolCall) -> "ApplyPatchActionIdentity":
        plan = _plan_for_call(step, call)
        changes = tuple(
            PlannedFileChangeIdentity(
                path=change.path,
                before_digest=_content_digest(change.before),
                after_digest=_content_digest(change.after),
            )
            for change in plan.changes
        )
        return cls(
            call_id=str(call.call_id or "").strip(),
            changes=changes,
        )

    def binding_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "changes": [change.binding_payload() for change in self.changes],
        }

    def instance_payload(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            **self.binding_payload(),
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical_bytes(self.binding_payload())).hexdigest()


__all__ = [
    "ApplyPatchActionIdentity",
    "PlannedFileChangeIdentity",
]
