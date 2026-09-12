from __future__ import annotations

"""Explicit network authorization and per-process enforcement context."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum

from .contracts import PermissionMode
from .permissions import ApprovalPolicy, PermissionDecision, PermissionSnapshot


NETWORK_POLICY_VERSION = 2
_NETWORK_ACCESS_GRANTED: ContextVar[bool] = ContextVar(
    "loom_network_access_granted",
    default=False,
)


class NetworkAccess(str, Enum):
    DENY = "deny"
    ON_REQUEST = "on-request"
    ALLOW = "allow"


@dataclass(frozen=True, slots=True)
class NetworkPolicyEvaluation:
    access: NetworkAccess
    decision: PermissionDecision
    reason: str


@contextmanager
def network_access_scope(granted: bool):
    """Bind the resolved network grant only while one tool starts its process."""

    token = _NETWORK_ACCESS_GRANTED.set(bool(granted))
    try:
        yield
    finally:
        _NETWORK_ACCESS_GRANTED.reset(token)


def current_network_access_granted() -> bool:
    return bool(_NETWORK_ACCESS_GRANTED.get())


class NetworkPolicy:
    version = NETWORK_POLICY_VERSION

    @staticmethod
    def access_for(snapshot: PermissionSnapshot) -> NetworkAccess:
        if snapshot.mode is PermissionMode.FULL_ACCESS:
            return NetworkAccess.ALLOW
        if snapshot.approval_policy is ApprovalPolicy.ON_REQUEST:
            return NetworkAccess.ON_REQUEST
        return NetworkAccess.DENY

    def evaluate(
        self,
        snapshot: PermissionSnapshot,
        *,
        requested: bool,
    ) -> NetworkPolicyEvaluation:
        if not requested:
            return NetworkPolicyEvaluation(
                access=self.access_for(snapshot),
                decision=PermissionDecision.ALLOW,
                reason="The command does not request network access.",
            )

        access = self.access_for(snapshot)
        if access is NetworkAccess.ALLOW:
            return NetworkPolicyEvaluation(
                access=access,
                decision=PermissionDecision.ALLOW,
                reason="Full-access mode permits network-capable commands.",
            )
        if access is NetworkAccess.ON_REQUEST:
            return NetworkPolicyEvaluation(
                access=access,
                decision=PermissionDecision.APPROVAL,
                reason="Network capability requires explicit approval in the active permission mode.",
            )
        return NetworkPolicyEvaluation(
            access=access,
            decision=PermissionDecision.DENY,
            reason="Network capability is disabled by the active permission mode.",
        )


__all__ = [
    "NETWORK_POLICY_VERSION",
    "NetworkAccess",
    "NetworkPolicy",
    "NetworkPolicyEvaluation",
    "current_network_access_granted",
    "network_access_scope",
]
