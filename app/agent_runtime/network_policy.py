from __future__ import annotations

"""Explicit network authorization policy shared by command-aware execution.

This layer answers whether a tool call that *intends* to use the network may run.
OS-level egress containment remains a separate sandbox concern; keeping the two
concepts separate avoids treating an unavailable sandbox backend as permission.
"""

from dataclasses import dataclass
from enum import Enum

from .contracts import PermissionMode
from .permissions import ApprovalPolicy, PermissionDecision, PermissionSnapshot


NETWORK_POLICY_VERSION = 1


class NetworkAccess(str, Enum):
    DENY = "deny"
    ON_REQUEST = "on-request"
    ALLOW = "allow"


@dataclass(frozen=True, slots=True)
class NetworkPolicyEvaluation:
    access: NetworkAccess
    decision: PermissionDecision
    reason: str


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
                reason="Network access requires explicit approval in the active permission mode.",
            )
        return NetworkPolicyEvaluation(
            access=access,
            decision=PermissionDecision.DENY,
            reason="Network access is disabled by the active permission mode.",
        )


__all__ = [
    "NETWORK_POLICY_VERSION",
    "NetworkAccess",
    "NetworkPolicy",
    "NetworkPolicyEvaluation",
]
