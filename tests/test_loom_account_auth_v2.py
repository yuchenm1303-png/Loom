from pathlib import Path

import pytest

from services.loom_account import server as account_server
from services.loom_account.server import AccountApplication, AccountConfig, AccountError, AccountStore


def _verified_app(tmp_path: Path) -> AccountApplication:
    return AccountApplication(AccountStore(AccountConfig(
        db_path=tmp_path / "accounts.db",
        email_provider="test",
        legacy_registration_enabled=True,
        google_client_id="google-id",
        google_client_secret="google-secret",
        github_client_id="github-id",
        github_client_secret="github-secret",
    )))


def test_resend_delivery_uses_application_user_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import json
    from urllib.error import HTTPError

    app = AccountApplication(AccountStore(AccountConfig(
        db_path=tmp_path / "accounts.db",
        email_provider="resend",
        email_from="Loom <account@example.com>",
        resend_api_key="test-key",
    )))
    requests = []

    def provider(request, timeout):
        # Reproduce the provider edge's rejection of unidentified urllib requests.
        if request.get_header("User-agent") != "Loom-Account/1.0":
            raise HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b"error code: 1010"))
        requests.append(request)
        response = io.BytesIO(b'{"id":"test-email"}')
        response.status = 200
        return response

    monkeypatch.setattr(account_server, "urlopen", provider)
    app.register_start({"email": "new@example.com", "password": "correct-horse"}, "client")

    assert len(requests) == 1
    request = requests[0]
    assert request.full_url == "https://api.resend.com/emails"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer test-key"
    payload = json.loads(request.data)
    assert payload["to"] == ["new@example.com"]
    assert payload["subject"] == "Verify your Loom account"


def test_verified_registration_requires_code_and_consumes_it_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(account_server.secrets, "randbelow", lambda _limit: 123456)
    app = _verified_app(tmp_path)
    started = app.register_start({"email": "Verify@Example.com", "password": "correct-horse"}, "client")
    challenge = started["challenge"]
    assert challenge["email"] == "verify@example.com"
    assert challenge["purpose"] == "register"
    assert app.store.user_by_email("verify@example.com") is None

    verified = app.verify_email({"challenge_id": challenge["id"], "code": "123456"}, "client")
    assert verified["user"]["email_verified"] is True
    assert verified["access_token"].startswith("loom_access_")

    with pytest.raises(AccountError) as reused:
        app.verify_email({"challenge_id": challenge["id"], "code": "123456"}, "client")
    assert reused.value.code == "INVALID_CODE"


def test_wrong_verification_code_does_not_create_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(account_server.secrets, "randbelow", lambda _limit: 654321)
    app = _verified_app(tmp_path)
    started = app.register_start({"email": "user@example.com", "password": "abcdefgh"}, "client")
    with pytest.raises(AccountError) as invalid:
        app.verify_email({"challenge_id": started["challenge"]["id"], "code": "000000"}, "client")
    assert invalid.value.code == "INVALID_CODE"
    assert app.store.user_by_email("user@example.com") is None


def test_password_reset_revokes_old_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(account_server.secrets, "randbelow", lambda _limit: 222222)
    app = _verified_app(tmp_path)
    original = app.register({"email": "reset@example.com", "password": "old-password"}, "client")
    forgot = app.forgot_password({"email": "reset@example.com"}, "client")
    reset = app.reset_password({
        "challenge_id": forgot["challenge"]["id"],
        "code": "222222",
        "password": "new-password",
    }, "client")
    assert reset["user"]["email"] == "reset@example.com"

    with pytest.raises(AccountError) as old_session:
        app.me(f"Bearer {original['access_token']}")
    assert old_session.value.code == "INVALID_TOKEN"

    logged_in = app.login({"email": "reset@example.com", "password": "new-password"}, "client")
    assert logged_in["user"]["id"] == reset["user"]["id"]


def test_unknown_reset_email_is_not_disclosed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(account_server.secrets, "randbelow", lambda _limit: 333333)
    app = _verified_app(tmp_path)
    result = app.forgot_password({"email": "missing@example.com"}, "client")
    assert result["challenge"]["purpose"] == "password_reset"
    with pytest.raises(AccountError) as invalid:
        app.reset_password({
            "challenge_id": result["challenge"]["id"],
            "code": "333333",
            "password": "new-password",
        }, "client")
    assert invalid.value.code == "INVALID_CODE"


def test_oauth_identity_links_by_verified_email_and_exchange_is_one_time(tmp_path: Path) -> None:
    app = _verified_app(tmp_path)
    password_user = app.register({"email": "same@example.com", "password": "abcdefgh"}, "client")["user"]
    oauth_user = app.store.upsert_oauth_user(
        "google", "google-subject", "same@example.com", email_verified=True, display_name="Same Person"
    )
    assert oauth_user["id"] == password_user["id"]

    code = app.store.create_oauth_exchange(oauth_user["id"])
    session = app.oauth_exchange({"code": code}, "client")
    assert session["user"]["id"] == password_user["id"]
    with pytest.raises(AccountError) as reused:
        app.oauth_exchange({"code": code}, "client")
    assert reused.value.code == "OAUTH_CODE_INVALID"


def test_capabilities_only_advertise_configured_auth_methods(tmp_path: Path) -> None:
    plain = AccountApplication(AccountStore(AccountConfig(db_path=tmp_path / "plain.db")))
    assert plain.capabilities() == {
        "emailVerification": False,
        "passwordReset": False,
        "google": False,
        "github": False,
        "legacyRegistration": True,
    }
    configured = _verified_app(tmp_path)
    caps = configured.capabilities()
    assert caps["emailVerification"] is True
    assert caps["passwordReset"] is True
    assert caps["google"] is True
    assert caps["github"] is True


def test_desktop_oauth_return_is_exact_and_nonce_bound(tmp_path: Path) -> None:
    app = _verified_app(tmp_path)
    nonce = "f" * 48
    callback = "loom://auth/callback?nonce=" + nonce
    assert app._safe_return_to(callback) == callback
    assert app._append_query(callback, loom_oauth_provider="google", loom_oauth_code="once") == (
        callback + "&loom_oauth_provider=google&loom_oauth_code=once"
    )
    for unsafe in (
        "loom://evil/callback?nonce=" + nonce,
        "loom://auth/anything?nonce=" + nonce,
        "loom://auth/callback?nonce=short",
        "loom://auth/callback?nonce=" + nonce + "&loom_oauth_code=forged",
        "loom://auth/callback?nonce=" + nonce + "#fragment",
        "loom://auth:1234/callback?nonce=" + nonce,
        "https://evil.example/redirect",
    ):
        assert app._safe_return_to(unsafe) != unsafe
