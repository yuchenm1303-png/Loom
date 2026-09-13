from __future__ import annotations

from app.agent_runtime.mcp_approval import McpToolApprovalKey, McpToolApprovalSessionCache


def test_mcp_approval_key_uses_server_tool_and_provenance_not_arguments():
    key = McpToolApprovalKey(
        server="github",
        plugin_id="plugin-1",
        connector_id="connector-1",
        link_id="link-1",
        tool_name="create_issue",
    )
    same = McpToolApprovalKey(
        server="github",
        plugin_id="plugin-1",
        connector_id="connector-1",
        link_id="link-1",
        tool_name="create_issue",
    )

    assert key == same


def test_mcp_session_approval_does_not_cross_server_tool_or_connector_identity():
    cache = McpToolApprovalSessionCache()
    approved = McpToolApprovalKey("github", None, "connector-1", "link-1", "create_issue")
    cache.approve_for_session(approved)

    assert cache.contains(approved)
    assert not cache.contains(
        McpToolApprovalKey("github", None, "connector-2", "link-1", "create_issue")
    )
    assert not cache.contains(
        McpToolApprovalKey("github", None, "connector-1", "link-1", "delete_issue")
    )
    assert not cache.contains(
        McpToolApprovalKey("other", None, "connector-1", "link-1", "create_issue")
    )
