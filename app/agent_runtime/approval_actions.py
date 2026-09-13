from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from app.ai import ToolCall

from .apply_patch_action import ApplyPatchActionIdentity, ApplyPatchApprovalCacheKey
from .execution_action import ExecActionIdentity, ExecApprovalCacheKey, execution_action_for
from .permissions import AdditionalPermissionProfile, SandboxPermissions


class ReviewDecision(str, Enum):
    """Core review outcomes used by Loom's approval layer.

    Policy-amendment and network/MCP-specific variants stay with their owning
    subsystems. The common outcomes intentionally mirror Codex protocol names.
    """

    APPROVED = "approved"
    APPROVED_FOR_SESSION = "approved_for_session"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
    ABORTED = "aborted"


class ApprovalActionKind(str, Enum):
    EXEC_COMMAND = "exec_command"
    APPLY_PATCH = "apply_patch"


ApprovalCacheKey = ExecApprovalCacheKey | ApplyPatchApprovalCacheKey


def _key_payload(key: ApprovalCacheKey) -> dict[str, object]:
    if isinstance(key, ExecApprovalCacheKey):
        return {"kind": "exec_command", **key.canonical()}
    if isinstance(key, ApplyPatchApprovalCacheKey):
        return {
            "kind": "apply_patch",
            "environment_id": key.environment_id,
            "path": key.path,
        }
    raise TypeError(f"unsupported approval cache key: {type(key).__name__}")


def approval_cache_digest(
    key: ApprovalCacheKey,
    *,
    policy_fingerprint: str = "",
) -> str:
    """Stable session-cache identity.

    Codex's action cache key is independent of call id. Loom additionally binds
    an optional policy fingerprint at the store boundary so a changed exec-policy
    snapshot cannot inherit a decision made under an older ruleset.
    """

    payload = {
        "key": _key_payload(key),
        "policy_fingerprint": str(policy_fingerprint or ""),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecApprovalAction:
    call_id: str
    environment_id: str
    command: tuple[str, ...]
    cwd: str
    sandbox_permissions: SandboxPermissions
    additional_permissions: AdditionalPermissionProfile | None
    justification: str
    tty: bool
    proposed_prefix_rule: tuple[str, ...] = ()

    @property
    def kind(self) -> ApprovalActionKind:
        return ApprovalActionKind.EXEC_COMMAND

    @classmethod
    def from_identity(
        cls,
        identity: ExecActionIdentity,
        *,
        environment_id: str = "local",
    ) -> "ExecApprovalAction":
        return cls(
            call_id=identity.call_id,
            environment_id=str(environment_id or "local"),
            command=identity.argv,
            cwd=identity.resolved_cwd,
            sandbox_permissions=identity.sandbox_permissions,
            additional_permissions=identity.additional_permissions,
            justification=identity.justification,
            tty=identity.pty,
            proposed_prefix_rule=identity.prefix_rule,
        )

    def cache_keys(self) -> tuple[ExecApprovalCacheKey, ...]:
        identity = ExecApprovalCacheKey(
            environment_id=self.environment_id,
            executable=self.command[0] if self.command else None,
            command=self.command,
            cwd=self.cwd,
            tty=self.tty,
            sandbox_permissions=self.sandbox_permissions,
            additional_permissions=self.additional_permissions,
        )
        return (identity,)


@dataclass(frozen=True, slots=True)
class ApplyPatchApprovalAction:
    call_id: str
    environment_id: str
    cwd: str
    files: tuple[str, ...]
    request_digest: str

    @property
    def kind(self) -> ApprovalActionKind:
        return ApprovalActionKind.APPLY_PATCH

    @classmethod
    def from_identity(
        cls,
        identity: ApplyPatchActionIdentity,
        *,
        cwd: str,
        environment_id: str = "local",
    ) -> "ApplyPatchApprovalAction":
        return cls(
            call_id=identity.call_id,
            environment_id=str(environment_id or "local"),
            cwd=str(cwd),
            files=identity.resolved_paths,
            request_digest=identity.request_digest,
        )

    def cache_keys(self) -> tuple[ApplyPatchApprovalCacheKey, ...]:
        return tuple(
            ApplyPatchApprovalCacheKey(
                environment_id=self.environment_id,
                path=path,
            )
            for path in self.files
        )


ApprovalAction = ExecApprovalAction | ApplyPatchApprovalAction


def approval_action_for(
    step,
    call: ToolCall,
    *,
    environment_id: str = "local",
) -> ApprovalAction | None:
    identity = execution_action_for(step, call)
    if isinstance(identity, ExecActionIdentity):
        return ExecApprovalAction.from_identity(identity, environment_id=environment_id)
    if isinstance(identity, ApplyPatchActionIdentity):
        return ApplyPatchApprovalAction.from_identity(
            identity,
            cwd=step.world_state.workspace_dir,
            environment_id=environment_id,
        )
    return None


class ApprovalDecisionStore:
    """In-process ApprovedForSession cache keyed by structured action semantics."""

    def __init__(self) -> None:
        self._decisions: dict[str, ReviewDecision] = {}

    def lookup(
        self,
        keys: Iterable[ApprovalCacheKey],
        *,
        policy_fingerprint: str = "",
    ) -> ReviewDecision | None:
        values = tuple(keys)
        if not values:
            return None
        decisions = tuple(
            self._decisions.get(
                approval_cache_digest(key, policy_fingerprint=policy_fingerprint)
            )
            for key in values
        )
        if decisions and all(
            decision is ReviewDecision.APPROVED_FOR_SESSION
            for decision in decisions
        ):
            return ReviewDecision.APPROVED_FOR_SESSION
        return None

    def record(
        self,
        keys: Iterable[ApprovalCacheKey],
        decision: ReviewDecision,
        *,
        policy_fingerprint: str = "",
    ) -> None:
        resolved = ReviewDecision(decision)
        # Codex only retains the explicit session-scoped approval. One-shot
        # approval, denial, timeout and abort do not become reusable grants.
        if resolved is not ReviewDecision.APPROVED_FOR_SESSION:
            return
        for key in tuple(keys):
            self._decisions[
                approval_cache_digest(key, policy_fingerprint=policy_fingerprint)
            ] = resolved

    def clear(self) -> None:
        self._decisions.clear()


__all__ = [
    "ApprovalAction",
    "ApprovalActionKind",
    "ApprovalCacheKey",
    "ApprovalDecisionStore",
    "ApplyPatchApprovalAction",
    "ExecApprovalAction",
    "ReviewDecision",
    "approval_action_for",
    "approval_cache_digest",
]
