from pathlib import Path

import pytest

from services.loom_account.server import (
    AccountApplication,
    AccountConfig,
    AccountError,
    AccountStore,
)


def _app(tmp_path: Path, *, access_ttl: int = 900, refresh_ttl: int = 3600) -> AccountApplication:
    store = AccountStore(
        AccountConfig(
            db_path=tmp_path / "accounts.db",
            access_ttl_seconds=access_ttl,
            refresh_ttl_seconds=refresh_ttl,
        )
    )
    return AccountApplication(store)


def test_register_login_refresh_and_logout(tmp_path: Path) -> None:
    app = _app(tmp_path)

    registered = app.register(
        {"email": "Test@Example.com", "password": "correct-horse"},
        "127.0.0.1",
    )
    assert registered["user"]["email"] == "test@example.com"
    assert registered["access_token"].startswith("loom_access_")
    assert registered["refresh_token"].startswith("loom_refresh_")
    assert app.me(f"Bearer {registered['access_token']}")["user"]["id"] == registered["user"]["id"]

    logged_in = app.login(
        {"email": "test@example.com", "password": "correct-horse"},
        "127.0.0.2",
    )
    assert logged_in["user"]["id"] == registered["user"]["id"]

    refreshed = app.refresh(
        {"refresh_token": logged_in["refresh_token"]},
        "127.0.0.2",
    )
    assert refreshed["access_token"] != logged_in["access_token"]
    assert refreshed["refresh_token"] != logged_in["refresh_token"]

    app.logout({"refresh_token": refreshed["refresh_token"]})
    with pytest.raises(AccountError) as caught:
        app.refresh({"refresh_token": refreshed["refresh_token"]}, "127.0.0.2")
    assert caught.value.code == "INVALID_REFRESH_TOKEN"


def test_duplicate_email_and_bad_password_fail_closed(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app.register({"email": "user@example.com", "password": "abcdefgh"}, "a")

    with pytest.raises(AccountError) as duplicate:
        app.register({"email": "USER@example.com", "password": "abcdefgh"}, "b")
    assert duplicate.value.code == "EMAIL_EXISTS"

    with pytest.raises(AccountError) as bad_password:
        app.login({"email": "user@example.com", "password": "wrong"}, "c")
    assert bad_password.value.code == "INVALID_CREDENTIALS"


def test_tokens_are_not_stored_in_plaintext(tmp_path: Path) -> None:
    app = _app(tmp_path)
    result = app.register({"email": "user@example.com", "password": "abcdefgh"}, "a")

    raw = (tmp_path / "accounts.db").read_bytes()
    assert result["access_token"].encode() not in raw
    assert result["refresh_token"].encode() not in raw


def test_relay_credential_survives_rotation_but_not_revocation(tmp_path):
    app = _app(tmp_path)
    session = app.register({"email": "relay@example.com", "password": "abcdefgh"}, "a")
    credential = app.relay_credential(f"Bearer {session['access_token']}")
    proof = credential["relay_token"]
    assert proof.startswith("loom_relay_")
    with app.store._connect() as db:
        stored = db.execute("SELECT token_hash FROM relay_credentials").fetchone()[0]
    assert stored != proof and not stored.startswith("loom_relay_")
    rotated = app.refresh({"refresh_token": session["refresh_token"]}, "a")
    assert app.relay_me(f"Bearer {proof}")["user"]["id"] == session["user"]["id"]
    # Relay proof does not grant login/model credential privileges.
    with pytest.raises(AccountError):
        app.me(f"Bearer {proof}")
    with pytest.raises(AccountError):
        app.model_access(f"Bearer {proof}")
    app.logout({"refresh_token": rotated["refresh_token"]})
    with pytest.raises(AccountError, match="Relay authorization"):
        app.relay_me(f"Bearer {proof}")


def test_relay_credential_respects_disabled_account_and_session_expiry(tmp_path, monkeypatch):
    import services.loom_account.server as module
    wall = [1000]
    monkeypatch.setattr(module, "_now", lambda: wall[0])
    app = _app(tmp_path, refresh_ttl=60)
    session = app.register({"email": "relay@example.com", "password": "abcdefgh"}, "a")
    proof = app.relay_credential(f"Bearer {session['access_token']}")["relay_token"]
    with app.store._connect() as db:
        db.execute("UPDATE users SET status='disabled'")
    with pytest.raises(AccountError) as disabled:
        app.relay_me(f"Bearer {proof}")
    assert disabled.value.code == "ACCOUNT_DISABLED"
    with app.store._connect() as db:
        db.execute("UPDATE users SET status='active'")
    wall[0] = 1061
    with pytest.raises(AccountError) as expired:
        app.relay_me(f"Bearer {proof}")
    assert expired.value.code == "INVALID_RELAY_TOKEN"


def test_profile_can_update_nickname_and_avatar(tmp_path: Path) -> None:
    import base64

    app = _app(tmp_path)
    session = app.register({"email": "profile@example.com", "password": "abcdefgh"}, "a")
    avatar = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"avatar").decode("ascii")

    updated = app.update_profile(
        {"display_name": "Yuchen", "avatar_data_url": avatar},
        f"Bearer {session['access_token']}",
    )["user"]

    assert updated["display_name"] == "Yuchen"
    assert updated["avatar_data_url"] == avatar
    current = app.me(f"Bearer {session['access_token']}")["user"]
    assert current["display_name"] == "Yuchen"
    assert current["avatar_data_url"] == avatar


def test_profile_rejects_unsafe_avatar_and_long_nickname(tmp_path: Path) -> None:
    app = _app(tmp_path)
    session = app.register({"email": "profile@example.com", "password": "abcdefgh"}, "a")
    authorization = f"Bearer {session['access_token']}"

    with pytest.raises(AccountError) as bad_avatar:
        app.update_profile({"avatar_data_url": "data:text/plain;base64,SGVsbG8="}, authorization)
    assert bad_avatar.value.code == "AVATAR_INVALID"

    with pytest.raises(AccountError) as long_name:
        app.update_profile({"display_name": "x" * 49}, authorization)
    assert long_name.value.code == "DISPLAY_NAME_TOO_LONG"


def test_automation_access_defaults_disabled_and_admin_controls_each_capability(tmp_path: Path) -> None:
    app = _app(tmp_path)
    owner = app.register({"email": "owner@example.com", "password": "abcdefgh"}, "owner")
    user = app.register({"email": "user@example.com", "password": "abcdefgh"}, "user")
    with app.store._connect() as db:
        db.execute("UPDATE users SET role='owner' WHERE id=?", (owner["user"]["id"],))

    user_auth = f"Bearer {user['access_token']}"
    owner_auth = f"Bearer {owner['access_token']}"
    initial = app.tool_access(user_auth)["access"]
    assert initial == {
        "computerUse": False,
        "browserUse": False,
        "source": "default",
        "updated_at": None,
        "updated_by": None,
    }

    granted = app.admin_set_user_tool_access(
        {
            "user_id": user["user"]["id"],
            "computerUse": True,
            "browserUse": False,
        },
        owner_auth,
    )["access"]
    assert granted["computerUse"] is True
    assert granted["browserUse"] is False
    assert granted["source"] == "override"

    credential = app.model_credential(user_auth)["model_token"]
    assert app.tool_access(f"Bearer {credential}")["access"]["computerUse"] is True

    revoked = app.admin_set_user_tool_access(
        {
            "user_id": user["user"]["id"],
            "computerUse": False,
            "browserUse": False,
        },
        owner_auth,
    )["access"]
    assert revoked["computerUse"] is False
    assert revoked["browserUse"] is False


def test_admin_ban_revokes_credentials_blocks_login_and_requires_explicit_unban(tmp_path: Path) -> None:
    app = _app(tmp_path)
    owner = app.register({"email": "owner@example.com", "password": "abcdefgh"}, "owner")
    user = app.register({"email": "user@example.com", "password": "abcdefgh"}, "user")
    with app.store._connect() as db:
        db.execute("UPDATE users SET role='owner' WHERE id=?", (owner["user"]["id"],))

    owner_auth = f"Bearer {owner['access_token']}"
    user_auth = f"Bearer {user['access_token']}"
    model_token = app.model_credential(user_auth)["model_token"]
    relay_token = app.relay_credential(user_auth)["relay_token"]

    banned = app.admin_ban_user(
        user["user"]["id"],
        {"reason": "Repeated automation abuse"},
        owner_auth,
    )["user"]
    assert banned["status"] == "banned"
    assert banned["ban_reason"] == "Repeated automation abuse"
    assert banned["banned_at"] is not None
    assert banned["banned_by"] == owner["user"]["id"]

    with pytest.raises(AccountError) as login_error:
        app.login({"email": "user@example.com", "password": "abcdefgh"}, "user-after-ban")
    assert login_error.value.code == "ACCOUNT_BANNED"

    with pytest.raises(AccountError) as access_error:
        app.me(user_auth)
    assert access_error.value.code == "INVALID_TOKEN"

    with pytest.raises(AccountError) as model_error:
        app.model_access(f"Bearer {model_token}")
    assert model_error.value.code == "INVALID_MODEL_TOKEN"

    with pytest.raises(AccountError) as relay_error:
        app.relay_me(f"Bearer {relay_token}")
    assert relay_error.value.code == "INVALID_RELAY_TOKEN"

    with pytest.raises(AccountError) as issue_error:
        app.store.create_session(user["user"]["id"])
    assert issue_error.value.code == "ACCOUNT_BANNED"

    with pytest.raises(AccountError) as legacy_enable:
        app.admin_set_user_status_by_id(user["user"]["id"], "active", owner_auth)
    assert legacy_enable.value.code == "ACCOUNT_BANNED_USE_UNBAN"

    unbanned = app.admin_unban_user(user["user"]["id"], owner_auth)["user"]
    assert unbanned["status"] == "active"
    assert unbanned["ban_reason"] == ""
    assert unbanned["banned_at"] is None
    assert unbanned["banned_by"] is None
    assert app.login({"email": "user@example.com", "password": "abcdefgh"}, "user-after-unban")["user"]["id"] == user["user"]["id"]


def test_admin_ban_requires_reason_and_owner_for_privileged_targets(tmp_path: Path) -> None:
    app = _app(tmp_path)
    owner = app.register({"email": "owner@example.com", "password": "abcdefgh"}, "owner")
    admin = app.register({"email": "admin@example.com", "password": "abcdefgh"}, "admin")
    user = app.register({"email": "user@example.com", "password": "abcdefgh"}, "user")
    with app.store._connect() as db:
        db.execute("UPDATE users SET role='owner' WHERE id=?", (owner["user"]["id"],))
        db.execute("UPDATE users SET role='admin' WHERE id=?", (admin["user"]["id"],))

    owner_auth = f"Bearer {owner['access_token']}"
    admin_auth = f"Bearer {admin['access_token']}"

    with pytest.raises(AccountError) as missing_reason:
        app.admin_ban_user(user["user"]["id"], {"reason": "   "}, owner_auth)
    assert missing_reason.value.code == "BAN_REASON_REQUIRED"

    with pytest.raises(AccountError) as privileged:
        app.admin_ban_user(owner["user"]["id"], {"reason": "nope"}, admin_auth)
    assert privileged.value.code == "OWNER_REQUIRED"

    with pytest.raises(AccountError) as self_ban:
        app.admin_ban_user(owner["user"]["id"], {"reason": "mistake"}, owner_auth)
    assert self_ban.value.code == "SELF_BAN_FORBIDDEN"


def test_unban_restores_the_status_that_existed_before_ban(tmp_path: Path) -> None:
    app = _app(tmp_path)
    owner = app.register({"email": "owner2@example.com", "password": "abcdefgh"}, "owner")
    user = app.register({"email": "disabled@example.com", "password": "abcdefgh"}, "user")
    with app.store._connect() as db:
        db.execute("UPDATE users SET role='owner' WHERE id=?", (owner["user"]["id"],))

    owner_auth = f"Bearer {owner['access_token']}"
    disabled = app.admin_set_user_status_by_id(user["user"]["id"], "disabled", owner_auth)["user"]
    assert disabled["status"] == "disabled"

    banned = app.admin_ban_user(
        user["user"]["id"],
        {"reason": "Escalated enforcement"},
        owner_auth,
    )["user"]
    assert banned["status"] == "banned"
    assert banned["ban_previous_status"] == "disabled"

    restored = app.admin_unban_user(user["user"]["id"], owner_auth)["user"]
    assert restored["status"] == "disabled"
