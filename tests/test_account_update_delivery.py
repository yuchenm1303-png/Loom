"""Per-account Loom software update delivery policy."""
from __future__ import annotations

import sqlite3
import json
from http import HTTPStatus

import pytest

from services.loom_account.server import AccountApplication, AccountConfig, AccountError, AccountStore


def _accounts(tmp_path):
    store = AccountStore(AccountConfig(db_path=tmp_path / "accounts.db"))
    app = AccountApplication(store)
    owner = app.register({"email": "owner@example.com", "password": "correct-horse"}, "test")
    user = app.register({"email": "user@example.com", "password": "correct-horse"}, "test")
    with store._connect() as db:
        db.execute("UPDATE users SET role='owner' WHERE id=?", (owner["user"]["id"],))
    return app, owner, user


def test_update_permission_defaults_on_and_changes_only_selected_user(tmp_path):
    app, owner, user = _accounts(tmp_path)
    owner_token = f"Bearer {owner['access_token']}"
    user_token = f"Bearer {user['access_token']}"

    assert app.me(user_token)["user"]["updates_enabled"] is True
    assert app.store.admin_user(user["user"]["id"])["updates_enabled"] is True
    changed = app.admin_set_user_updates(
        {"user_id": user["user"]["id"], "enabled": False}, owner_token
    )["user"]
    assert changed["updates_enabled"] is False
    assert app.me(user_token)["user"]["updates_enabled"] is False
    assert app.me(owner_token)["user"]["updates_enabled"] is True
    assert next(u for u in app.admin_users(owner_token)["users"] if u["id"] == user["user"]["id"])["updates_enabled"] is False
    with app.store._connect() as db:
        last = db.execute("SELECT action, target_id, metadata_json FROM audit_logs ORDER BY id DESC LIMIT 1").fetchone()
    assert last["action"] == "user.update_access"
    assert last["target_id"] == str(user["user"]["id"])
    assert json.loads(last["metadata_json"]) == {"enabled": False}

    app.admin_set_user_updates({"user_id": user["user"]["id"], "enabled": True}, owner_token)
    assert app.me(user_token)["user"]["updates_enabled"] is True
    assert AccountStore(app.store.config).admin_user(user["user"]["id"])["updates_enabled"] is True


def test_update_access_is_admin_only_and_boolean(tmp_path):
    app, owner, user = _accounts(tmp_path)
    user_token = f"Bearer {user['access_token']}"
    owner_token = f"Bearer {owner['access_token']}"

    with pytest.raises(AccountError) as denied:
        app.admin_set_user_updates({"user_id": user["user"]["id"], "enabled": False}, user_token)
    assert denied.value.status == HTTPStatus.FORBIDDEN

    for invalid in ("false", 0, 1, None, [], {}):
        with pytest.raises(AccountError) as bad:
            app.admin_set_user_updates({"user_id": user["user"]["id"], "enabled": invalid}, owner_token)
        assert bad.value.code == "INVALID_UPDATE_ACCESS"

    with pytest.raises(AccountError) as missing:
        app.admin_set_user_updates({"user_id": 99999, "enabled": False}, owner_token)
    assert missing.value.code == "USER_NOT_FOUND"
    assert app.me(user_token)["user"]["updates_enabled"] is True


def test_old_account_database_migrates_to_updates_enabled(tmp_path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        role TEXT NOT NULL DEFAULT 'user',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""")
    db.execute(
        "INSERT INTO users(email,password_hash,created_at,updated_at) VALUES(?,?,?,?)",
        ("legacy@example.com", "hashed", 1, 1),
    )
    db.commit()
    db.close()
    store = AccountStore(AccountConfig(db_path=path))
    assert store.user_by_email("legacy@example.com")["updates_enabled"] is True
    assert store.admin_user(1)["updates_enabled"] is True


def test_admin_page_renders_account_specific_update_switch():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "services/loom_admin/static/admin.js").read_text(encoding="utf-8")
    assert 'data-user-updates="' in js
    assert 'id="detailUpdatesEnabled"' in js
    assert "request('/admin/users/updates'" in js
    assert "updates_enabled!==false" in js


def test_every_update_channel_checks_account_eligibility():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "desktop-react/electron"
    for module in ("updater.ts", "applicationUpdater.ts", "hostRuntimeUpdater.ts", "rendererUpdater.ts"):
        source = (root / module).read_text(encoding="utf-8")
        assert 'mayReceiveUpdates' in source, module
    installer = (root / "updater.ts").read_text(encoding="utf-8")
    assert "autoUpdater.autoInstallOnAppQuit = false" in installer
