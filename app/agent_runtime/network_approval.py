from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import RLock


class NetworkApprovalProtocol(str, Enum):
    """Protocols carried by Codex NetworkApprovalContext."""

    HTTP = "http"
    HTTPS = "https"
    SOCKS5_TCP = "socks5_tcp"
    SOCKS5_UDP = "socks5_udp"


class NetworkPolicyRuleAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class NetworkSessionDecision(str, Enum):
    ALLOW_FOR_SESSION = "allow_for_session"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class NetworkApprovalContext:
    """User-visible network approval identity.

    Deliberately excludes the port, matching Codex protocol semantics.  The
    port remains part of the concrete access/session-cache identity below.
    """

    host: str
    protocol: NetworkApprovalProtocol

    def __post_init__(self) -> None:
        host = str(self.host or "").strip()
        if not host:
            raise ValueError("network approval host must not be empty")
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "protocol", NetworkApprovalProtocol(self.protocol))


@dataclass(frozen=True, slots=True)
class NetworkPolicyAmendment:
    """Persistent policy amendment shape used by Codex: host + allow/deny."""

    host: str
    action: NetworkPolicyRuleAction

    def __post_init__(self) -> None:
        host = str(self.host or "").strip()
        if not host:
            raise ValueError("network policy amendment host must not be empty")
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "action", NetworkPolicyRuleAction(self.action))


@dataclass(frozen=True, slots=True)
class NetworkAccessIdentity:
    environment_id: str
    host: str
    protocol: NetworkApprovalProtocol
    port: int

    def __post_init__(self) -> None:
        environment_id = str(self.environment_id or "").strip()
        host = str(self.host or "").strip().lower()
        port = int(self.port)
        if not environment_id:
            raise ValueError("network environment_id must not be empty")
        if not host:
            raise ValueError("network host must not be empty")
        if not 1 <= port <= 65535:
            raise ValueError("network port must be between 1 and 65535")
        object.__setattr__(self, "environment_id", environment_id)
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "protocol", NetworkApprovalProtocol(self.protocol))
        object.__setattr__(self, "port", port)

    @property
    def approval_context(self) -> NetworkApprovalContext:
        return NetworkApprovalContext(host=self.host, protocol=self.protocol)


@dataclass(frozen=True, slots=True)
class PendingNetworkApprovalKey:
    """Generation-safe pending key: host identity + turn + execution."""

    host: NetworkAccessIdentity
    turn_id: str
    execution_id: str | None = None

    def __post_init__(self) -> None:
        turn_id = str(self.turn_id or "").strip()
        if not turn_id:
            raise ValueError("network approval turn_id must not be empty")
        object.__setattr__(self, "turn_id", turn_id)
        if self.execution_id is not None:
            execution_id = str(self.execution_id).strip()
            object.__setattr__(self, "execution_id", execution_id or None)


@dataclass(frozen=True, slots=True)
class ExecPolicyNetworkRuleAmendment:
    """Protocol-specific projection used when persisting a policy amendment."""

    host: str
    protocol: NetworkApprovalProtocol
    action: NetworkPolicyRuleAction
    justification: str


def execpolicy_network_rule_amendment(
    amendment: NetworkPolicyAmendment,
    context: NetworkApprovalContext,
    *,
    actual_host: str | None = None,
) -> ExecPolicyNetworkRuleAmendment:
    """Translate a reviewed amendment to the protocol-specific exec-policy rule.

    Codex carries protocol separately from NetworkPolicyAmendment, so the
    persisted decision must combine the amendment with the approval context.
    """

    host = str(actual_host or amendment.host or "").strip()
    if not host:
        raise ValueError("network policy rule host must not be empty")
    verb = "Allow" if amendment.action is NetworkPolicyRuleAction.ALLOW else "Deny"
    protocol_label = {
        NetworkApprovalProtocol.HTTP: "http",
        NetworkApprovalProtocol.HTTPS: "https_connect",
        NetworkApprovalProtocol.SOCKS5_TCP: "socks5_tcp",
        NetworkApprovalProtocol.SOCKS5_UDP: "socks5_udp",
    }[context.protocol]
    return ExecPolicyNetworkRuleAmendment(
        host=amendment.host,
        protocol=context.protocol,
        action=amendment.action,
        justification=f"{verb} {protocol_label} access to {host}",
    )


@dataclass(slots=True)
class NetworkApprovalSessionCache:
    """Codex-style session allow/deny cache, keyed by exact network access."""

    _approved: set[NetworkAccessIdentity] = field(default_factory=set, init=False, repr=False)
    _denied: set[NetworkAccessIdentity] = field(default_factory=set, init=False, repr=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def decision_for(self, identity: NetworkAccessIdentity) -> NetworkSessionDecision | None:
        with self._lock:
            # Fail closed: a recorded deny wins over a stale/competing allow.
            if identity in self._denied:
                return NetworkSessionDecision.DENY
            if identity in self._approved:
                return NetworkSessionDecision.ALLOW_FOR_SESSION
            return None

    def approve_for_session(self, identity: NetworkAccessIdentity) -> None:
        with self._lock:
            self._denied.discard(identity)
            self._approved.add(identity)

    def deny_for_session(self, identity: NetworkAccessIdentity) -> None:
        with self._lock:
            self._approved.discard(identity)
            self._denied.add(identity)

    def clear(self) -> None:
        with self._lock:
            self._approved.clear()
            self._denied.clear()


def allows_network_approval_flow(
    *,
    managed_network_active: bool,
    ask_for_approval: str,
    permission_profile_managed: bool,
) -> bool:
    """Pure integration guard mirroring Codex's network-approval gates.

    The central orchestrator remains responsible for supplying the actual
    approval policy and permission profile; this module does not weaken either.
    """

    return (
        bool(managed_network_active)
        and str(ask_for_approval or "").strip().casefold() != "never"
        and bool(permission_profile_managed)
    )


__all__ = [
    "ExecPolicyNetworkRuleAmendment",
    "NetworkAccessIdentity",
    "NetworkApprovalContext",
    "NetworkApprovalProtocol",
    "NetworkApprovalSessionCache",
    "NetworkPolicyAmendment",
    "NetworkPolicyRuleAction",
    "NetworkSessionDecision",
    "PendingNetworkApprovalKey",
    "allows_network_approval_flow",
    "execpolicy_network_rule_amendment",
]
