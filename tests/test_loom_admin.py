from pathlib import Path

import pytest

from services.loom_account.server import AccountApplication, AccountConfig, AccountError, AccountStore

PASSWORD = "correct-horse-battery"


def _app(tmp_path: Path) -> AccountApplication:
    return AccountApplication(AccountStore(AccountConfig(db_path=tmp_path / "accounts.db")))


def _set_role(app: AccountApplication, user_id: int, role: str) -> None:
    with app.store._connect() as db:
        db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def _token(payload: dict) -> str:
    return f"Bearer {payload['access_token']}"


def test_registration_always_defaults_to_user(tmp_path: Path) -> None:
    app = _app(tmp_path)
    created = app.register({"email": "user@example.com", "password": PASSWORD, "role": "owner"}, "ip")
    assert created["user"]["role"] == "user"


def test_admin_endpoints_allow_owner_and_admin_but_deny_user(tmp_path: Path) -> None:
    app = _app(tmp_path)
    owner = app.register({"email": "owner@example.com", "password": PASSWORD}, "a")
    admin = app.register({"email": "admin@example.com", "password": PASSWORD}, "b")
    user = app.register({"email": "user@example.com", "password": PASSWORD}, "c")
    _set_role(app, owner["user"]["id"], "owner")
    _set_role(app, admin["user"]["id"], "admin")
    assert app.admin_overview(_token(owner))["users"] == 3
    assert app.admin_overview(_token(admin))["admins"] == 1
    with pytest.raises(AccountError) as denied:
        app.admin_overview(_token(user))
    assert denied.value.status == 403
    assert denied.value.code == "ADMIN_REQUIRED"


def test_role_changes_remain_owner_only_and_admin_role_is_valid(tmp_path: Path) -> None:
    app = _app(tmp_path)
    owner = app.register({"email": "owner@example.com", "password": PASSWORD}, "a")
    admin = app.register({"email": "admin@example.com", "password": PASSWORD}, "b")
    user = app.register({"email": "user@example.com", "password": PASSWORD}, "c")
    _set_role(app, owner["user"]["id"], "owner")
    _set_role(app, admin["user"]["id"], "admin")
    promoted = app.admin_set_user_role({"user_id": user["user"]["id"], "role": "admin"}, _token(owner))["user"]
    assert promoted["role"] == "admin"
    with pytest.raises(AccountError) as denied:
        app.admin_set_user_role({"user_id": user["user"]["id"], "role": "user"}, _token(admin))
    assert denied.value.code == "OWNER_REQUIRED"


def test_last_owner_guard_is_preserved(tmp_path: Path) -> None:
    app = _app(tmp_path)
    owner = app.register({"email": "owner@example.com", "password": PASSWORD}, "a")
    _set_role(app, owner["user"]["id"], "owner")
    with pytest.raises(AccountError) as denied:
        app.admin_set_user_role({"user_id": owner["user"]["id"], "role": "admin"}, _token(owner))
    assert denied.value.code == "LAST_OWNER"


def test_admin_system_and_real_overview_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("LOOM_SMTP_HOST", "SMTP_HOST", "LOOM_SMTP_FROM", "SMTP_FROM"):
        monkeypatch.delenv(key, raising=False)
    app = _app(tmp_path)
    owner = app.register({"email": "owner@example.com", "password": PASSWORD}, "a")
    admin = app.register({"email": "admin@example.com", "password": PASSWORD}, "b")
    _set_role(app, owner["user"]["id"], "owner")
    _set_role(app, admin["user"]["id"], "admin")
    overview = app.admin_overview(_token(owner))
    assert overview["users"] == 2
    assert overview["owners"] == 1
    assert overview["admins"] == 1
    assert overview["active_sessions"] == 2
    assert overview["total_sessions"] == 2
    system = app.admin_system(_token(admin))
    assert system["account_api"]["status"] == "healthy"
    assert system["database"] == {"status": "healthy", "engine": "SQLite"}
    assert system["smtp"]["status"] == "not_configured"


def test_admin_frontend_vendors_original_usage_glass_system() -> None:
    root = Path(__file__).resolve().parents[1]
    html = (root / "services/loom_admin/static/index.html").read_text(encoding="utf-8")
    js = (root / "services/loom_admin/static/admin.js").read_text(encoding="utf-8")
    assert 'https://smirel.com/download/usage-admin-v1.css' in html
    assert 'https://smirel.com/download/usage-ops-v1.css' in html
    assert 'https://smirel.com/download/beach-wallpaper-v1.css' in html
    assert 'https://smirel.com/download/wallpaper-rain-anime-v1.png' in html
    assert 'loom-admin-primary-metrics' in html
    assert 'usage-range-control' in html
    assert 'loom-admin-overview-pulse' in html
    assert 'id="runs" data-admin-page="runs" hidden' in html
    assert 'id="user" data-admin-page="user" hidden' in html
    assert 'async function loadPageData(page' in js
    assert 'request(`/admin/users/${id}/agent-ops`)' in js
    assert '/admin/system' in js


def test_model_entitlements_default_and_override(tmp_path: Path) -> None:
    app=_app(tmp_path); owner=app.register({"email":"owner-models@example.com","password":PASSWORD},"a"); user=app.register({"email":"user-models@example.com","password":PASSWORD},"b")
    _set_role(app, owner["user"]["id"], "owner")
    default=app.model_access(_token(user))["access"]
    assert default["enabled"] is True and "Ling-3.0-flash" in default["models"] and default["source"] == "default"
    changed=app.admin_set_user_model_access({"user_id":user["user"]["id"],"enabled":True,"models":["Ling-3.0-tiny"]},_token(owner))["access"]
    assert changed["models"] == ["Ling-3.0-tiny"] and changed["source"] == "override"
    assert app.model_access(_token(user))["access"]["models"] == ["Ling-3.0-tiny"]

def test_admin_can_disable_all_builtin_models_for_one_user(tmp_path: Path) -> None:
    app=_app(tmp_path); owner=app.register({"email":"owner-disable@example.com","password":PASSWORD},"a"); user=app.register({"email":"user-disable@example.com","password":PASSWORD},"b")
    _set_role(app, owner["user"]["id"], "owner")
    result=app.admin_set_user_model_access({"user_id":user["user"]["id"],"enabled":False,"models":[]},_token(owner))["access"]
    assert result["enabled"] is False and app.model_access(_token(user))["access"]["enabled"] is False



def test_scoped_model_credential_is_tied_to_login_session(tmp_path: Path) -> None:
    app = _app(tmp_path)
    user = app.register({"email": "model-token@example.com", "password": PASSWORD}, "a")
    credential = app.model_credential(_token(user))
    model_token = credential["model_token"]
    assert model_token.startswith("loom_model_")
    assert app.model_access(f"Bearer {model_token}")["access"]["enabled"] is True

    app.logout({"refresh_token": user["refresh_token"]})
    with pytest.raises(AccountError) as denied:
        app.model_access(f"Bearer {model_token}")
    assert denied.value.code == "INVALID_MODEL_TOKEN"
