from pathlib import Path

import pytest

from services.loom_account.server import AccountApplication, AccountConfig, AccountError, AccountStore


def _app(tmp_path: Path) -> AccountApplication:
    return AccountApplication(AccountStore(AccountConfig(db_path=tmp_path / "accounts.db")))


def _promote(app: AccountApplication, user_id: int, role: str = "owner") -> None:
    with app.store._connect() as db:
        db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def test_admin_api_is_role_gated_and_owner_can_manage_users(tmp_path: Path) -> None:
    app = _app(tmp_path)
    owner = app.register({"email": "owner@example.com", "password": "abcdefgh"}, "owner-ip")
    user = app.register({"email": "user@example.com", "password": "abcdefgh"}, "user-ip")

    with pytest.raises(AccountError) as denied:
        app.admin_overview(f"Bearer {owner['access_token']}")
    assert denied.value.code == "ADMIN_REQUIRED"

    _promote(app, owner["user"]["id"])
    overview = app.admin_overview(f"Bearer {owner['access_token']}")
    assert overview["users"] == 2
    assert overview["owners"] == 1

    updated = app.admin_set_user_status(
        {"user_id": user["user"]["id"], "status": "disabled"},
        f"Bearer {owner['access_token']}",
    )["user"]
    assert updated["status"] == "disabled"

    events = app.admin_audit(f"Bearer {owner['access_token']}")["events"]
    assert events[0]["action"] == "user.status"


def test_owner_only_role_changes_and_last_owner_guard(tmp_path: Path) -> None:
    app = _app(tmp_path)
    owner = app.register({"email": "owner@example.com", "password": "abcdefgh"}, "a")
    admin = app.register({"email": "admin@example.com", "password": "abcdefgh"}, "b")
    _promote(app, owner["user"]["id"], "owner")
    _promote(app, admin["user"]["id"], "admin")

    with pytest.raises(AccountError) as denied:
        app.admin_set_user_role(
            {"user_id": owner["user"]["id"], "role": "user"},
            f"Bearer {admin['access_token']}",
        )
    assert denied.value.code == "OWNER_REQUIRED"

    with pytest.raises(AccountError) as last_owner:
        app.admin_set_user_role(
            {"user_id": owner["user"]["id"], "role": "admin"},
            f"Bearer {owner['access_token']}",
        )
    assert last_owner.value.code == "LAST_OWNER"


def test_feature_flags_are_audited(tmp_path: Path) -> None:
    app = _app(tmp_path)
    owner = app.register({"email": "owner@example.com", "password": "abcdefgh"}, "a")
    _promote(app, owner["user"]["id"])
    result = app.admin_set_feature_flag(
        {"key": "web.new_shell", "enabled": True, "value": {"rollout": 100}},
        f"Bearer {owner['access_token']}",
    )["flag"]
    assert result["enabled"] is True
    flags = app.admin_feature_flags(f"Bearer {owner['access_token']}")["flags"]
    assert flags[0]["key"] == "web.new_shell"


def test_admin_frontend_reuses_service_monitor_components() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "services/loom_admin/static/index.html").read_text(encoding="utf-8")
    css = (root / "services/loom_admin/static/admin.css").read_text(encoding="utf-8")
    assert "usage-summary-grid" in html
    assert "usage-activity-card" in html
    assert "usage-ops-grid" in html
    assert "https://smirel.com/download/usage-admin-v1.css" in html
    assert "wallpaper-rain-anime-v1.png" in css
    assert "smirel-logo.svg" in html
