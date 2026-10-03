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
