from __future__ import annotations

import os
import time
from pathlib import Path

from services.loom_account.server import AccountConfig
from services.loom_account.agent_ops import AgentOpsStore as AccountStore


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "services/loom_admin/static/index.html").read_text(encoding="utf-8")
JS = (ROOT / "services/loom_admin/static/admin.js").read_text(encoding="utf-8")
CSS = (ROOT / "services/loom_admin/static/admin.css").read_text(encoding="utf-8")


def _store(tmp_path):
    os.environ["LOOM_TELEMETRY_SECRET"] = "cards-secret"
    store = AccountStore(AccountConfig(db_path=tmp_path / "accounts.db"))
    owner = store.register_verified("owner@example.com", "test-hash")
    other = store.register_verified("other@example.com", "test-hash")
    with store._connect() as db:
        db.execute("UPDATE users SET role='owner' WHERE id=?", (owner["id"],))
    return store, owner, other


def test_user_operations_summary_is_account_scoped_and_visualization_ready(tmp_path):
    store, owner, other = _store(tmp_path)
    now = int(time.time())
    store.telemetry_device({
        "event": "connected", "user_id": owner["id"], "device_id": "owner-pc",
        "name": "Owner PC", "platform": "win32", "host_version": "1.2.3", "app_version": "0.2.0", "at": now - 120,
    })
    store.telemetry_device({
        "event": "connected", "user_id": other["id"], "device_id": "other-mac",
        "name": "Other Mac", "platform": "darwin", "host_version": "1.1.0", "app_version": "0.1.0", "at": now - 60,
    })
    store.telemetry_agent_event({"event":"thread.started","user_id":owner["id"],"device_id":"owner-pc","thread_id":"owner-thread","model":"gpt-test","provider":"openai","at":now-50})
    store.telemetry_agent_event({"event":"turn.started","user_id":owner["id"],"device_id":"owner-pc","thread_id":"owner-thread","turn_id":"owner-turn","at":now-45})
    store.telemetry_agent_event({"event":"tool.completed","user_id":owner["id"],"device_id":"owner-pc","thread_id":"owner-thread","turn_id":"owner-turn","call_id":"owner-call","event_key":"tool:owner-call","tool_name":"computer_use","at":now-40})
    store.telemetry_agent_event({"event":"turn.completed","user_id":owner["id"],"device_id":"owner-pc","thread_id":"owner-thread","turn_id":"owner-turn","status":"completed","usage":{"inputTokens":80,"outputTokens":40,"totalTokens":120},"at":now-30})

    payload = store.admin_users_operations()
    cards = {item["email"]: item for item in payload["users"]}
    owner_card = cards["owner@example.com"]
    other_card = cards["other@example.com"]

    assert owner_card["known_devices"] == 1
    assert owner_card["primary_device"]["device_id"] == "owner-pc"
    assert owner_card["runs_24h"] == 1
    assert owner_card["tokens_24h"] == 120
    assert owner_card["tool_calls_24h"] == 1
    assert owner_card["success_rate_24h"] == 100
    assert sum(bucket["runs"] for bucket in owner_card["activity_24h"]) == 1
    assert owner_card["top_tools_24h"] == [{"tool_name": "computer_use", "calls": 1}]

    assert other_card["known_devices"] == 1
    assert other_card["primary_device"]["device_id"] == "other-mac"
    assert other_card["runs_24h"] == 0
    assert other_card["tokens_24h"] == 0
    assert other_card["top_tools_24h"] == []


def test_users_page_is_card_first_and_keeps_detail_on_secondary_page():
    assert 'id="usersGrid"' in HTML
    assert 'id="userPresenceFilter"' in HTML
    assert 'id="userSort"' in HTML
    assert 'id="usersBody"' not in HTML
    assert "request('/admin/users-operations')" in JS
    assert "function userActivityBars(u)" in JS
    assert "function userToolMix(u)" in JS
    assert "function userPresence(u)" in JS
    assert "data-user-detail" in JS
    assert "request(`/admin/users/${id}/agent-ops`)" in JS


def test_user_cards_include_live_host_metrics_and_visuals():
    for marker in (
        ".loom-admin-user-card-grid",
        ".loom-admin-user-device",
        ".loom-admin-user-metric-grid",
        ".loom-admin-user-spark",
        ".loom-admin-user-ring",
        ".loom-admin-user-tool-bar",
    ):
        assert marker in CSS
    assert "PRIMARY HOST" in JS
    assert "24H ACTIVITY" in JS
    assert "TOOL MIX · 24H" in JS
    assert "success_rate_24h" in JS


def test_user_and_device_health_visuals_are_present_and_secondary_page_is_card_based():
    assert 'id="devicesGrid"' in HTML
    assert 'id="devicesBody"' not in HTML
    for marker in (
        "function hostHealthMini(device)",
        "function deviceHealthCard(d)",
        "function healthTrend(device)",
        "SYSTEM HEALTH",
        "24H RESOURCE TREND",
        "WAITING FOR HOST HEALTH V2",
        "health_history_24h",
    ):
        assert marker in JS
    for marker in (
        ".loom-admin-host-health-mini",
        ".loom-admin-health-metric",
        ".loom-admin-capability",
        ".loom-admin-device-card-grid",
        ".loom-admin-device-health-trend",
    ):
        assert marker in CSS
