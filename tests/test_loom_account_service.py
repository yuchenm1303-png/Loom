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
