from __future__ import annotations

from app.agent_runtime.network_approval import (
    NetworkAccessIdentity,
    NetworkApprovalContext,
    NetworkApprovalProtocol,
    NetworkApprovalSessionCache,
    NetworkPolicyAmendment,
    NetworkPolicyRuleAction,
    NetworkSessionDecision,
    PendingNetworkApprovalKey,
    allows_network_approval_flow,
    execpolicy_network_rule_amendment,
)


def test_network_approval_context_is_host_plus_protocol_only():
    identity = NetworkAccessIdentity(
        environment_id="env-1",
        host="Example.COM",
        protocol=NetworkApprovalProtocol.HTTPS,
        port=443,
    )

    assert identity.host == "example.com"
    assert identity.approval_context == NetworkApprovalContext(
        host="example.com",
        protocol=NetworkApprovalProtocol.HTTPS,
    )


def test_session_cache_key_includes_environment_host_protocol_and_port():
    cache = NetworkApprovalSessionCache()
    approved = NetworkAccessIdentity("env-1", "example.com", NetworkApprovalProtocol.HTTPS, 443)
    cache.approve_for_session(approved)

    assert cache.decision_for(approved) is NetworkSessionDecision.ALLOW_FOR_SESSION
    assert cache.decision_for(
        NetworkAccessIdentity("env-2", "example.com", NetworkApprovalProtocol.HTTPS, 443)
    ) is None
    assert cache.decision_for(
        NetworkAccessIdentity("env-1", "example.com", NetworkApprovalProtocol.HTTP, 443)
    ) is None
    assert cache.decision_for(
        NetworkAccessIdentity("env-1", "example.com", NetworkApprovalProtocol.HTTPS, 8443)
    ) is None
    assert cache.decision_for(
        NetworkAccessIdentity("env-1", "redirect.example.com", NetworkApprovalProtocol.HTTPS, 443)
    ) is None


def test_session_deny_removes_allow_and_wins_fail_closed():
    cache = NetworkApprovalSessionCache()
    identity = NetworkAccessIdentity("env", "example.com", NetworkApprovalProtocol.HTTP, 80)

    cache.approve_for_session(identity)
    cache.deny_for_session(identity)

    assert cache.decision_for(identity) is NetworkSessionDecision.DENY


def test_pending_key_separates_turn_and_execution_generation():
    host = NetworkAccessIdentity("env", "example.com", NetworkApprovalProtocol.HTTPS, 443)

    assert PendingNetworkApprovalKey(host, "turn-1", "exec-1") != PendingNetworkApprovalKey(
        host, "turn-1", "exec-2"
    )
    assert PendingNetworkApprovalKey(host, "turn-1", "exec-1") != PendingNetworkApprovalKey(
        host, "turn-2", "exec-1"
    )


def test_policy_amendment_combines_host_action_with_context_protocol():
    amendment = NetworkPolicyAmendment("example.com", NetworkPolicyRuleAction.ALLOW)
    context = NetworkApprovalContext("example.com", NetworkApprovalProtocol.HTTPS)

    rule = execpolicy_network_rule_amendment(amendment, context)

    assert rule.host == "example.com"
    assert rule.protocol is NetworkApprovalProtocol.HTTPS
    assert rule.action is NetworkPolicyRuleAction.ALLOW
    assert rule.justification == "Allow https_connect access to example.com"


def test_network_approval_flow_requires_managed_proxy_policy_and_permission_profile():
    assert allows_network_approval_flow(
        managed_network_active=True,
        ask_for_approval="on-request",
        permission_profile_managed=True,
    )
    assert not allows_network_approval_flow(
        managed_network_active=False,
        ask_for_approval="on-request",
        permission_profile_managed=True,
    )
    assert not allows_network_approval_flow(
        managed_network_active=True,
        ask_for_approval="never",
        permission_profile_managed=True,
    )
    assert not allows_network_approval_flow(
        managed_network_active=True,
        ask_for_approval="on-request",
        permission_profile_managed=False,
    )
