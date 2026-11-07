from __future__ import annotations

from http import HTTPStatus

import pytest

from services.loom_account.server import (
    AccountApplication,
    AccountConfig,
    AccountError,
    AccountStore,
)


@pytest.fixture
def app(tmp_path) -> AccountApplication:
    return AccountApplication(AccountStore(AccountConfig(db_path=tmp_path / "accounts.db")))


def _host(app: AccountApplication, email: str = "phone@example.com") -> str:
    host = app.register({"email": email, "password": "correct-horse-battery-staple"}, f"register-{email}")
    return f"Bearer {host['access_token']}"


def _claimed(app: AccountApplication, host: str) -> tuple[str, dict]:
    issued = app.issue_mobile_pair(host, {"device_id": "desk-1", "device_name": "Desk"}, "host")
    claim = app.claim_mobile_pair({"pairing_ticket": issued["pairing_ticket"], "phone_name": "Mate 50"}, "phone")
    return issued["pair_id"], claim


def _paired(app: AccountApplication, host: str) -> dict:
    pair_id, claim = _claimed(app, host)
    app.decide_mobile_pair(host, {"pair_id": pair_id, "approve": True})
    return app.poll_mobile_pair({"claim_token": claim["claim_token"]}, "phone")


def test_scanning_grants_nothing_until_the_computer_approves(app) -> None:
    host = _host(app)
    pair_id, claim = _claimed(app, host)

    assert claim["device_id"] == "desk-1" and len(claim["confirm_code"]) == 6
    assert "access_token" not in claim
    assert app.poll_mobile_pair({"claim_token": claim["claim_token"]}, "phone") == {"state": "claimed"}

    status = app.mobile_pair_status(host, {"pair_id": pair_id})
    assert status["confirm_code"] == claim["confirm_code"]
    assert status["phone_name"] == "Mate 50"

    app.decide_mobile_pair(host, {"pair_id": pair_id, "approve": True})
    session = app.poll_mobile_pair({"claim_token": claim["claim_token"]}, "phone")
    assert session["state"] == "approved" and session["access_token"]
    assert session["device_name"] == "Desk"


def test_denied_pairing_never_yields_tokens(app) -> None:
    host = _host(app)
    pair_id, claim = _claimed(app, host)
    app.decide_mobile_pair(host, {"pair_id": pair_id, "approve": False})
    assert app.poll_mobile_pair({"claim_token": claim["claim_token"]}, "phone") == {"state": "denied"}


def test_a_ticket_can_only_be_claimed_once(app) -> None:
    host = _host(app)
    issued = app.issue_mobile_pair(host, {}, "host")
    app.claim_mobile_pair({"pairing_ticket": issued["pairing_ticket"]}, "phone")
    with pytest.raises(AccountError) as error:
        app.claim_mobile_pair({"pairing_ticket": issued["pairing_ticket"]}, "thief")
    assert error.value.code == "INVALID_PAIRING_TICKET"


def test_an_approval_yields_one_session_only(app) -> None:
    host = _host(app)
    pair_id, claim = _claimed(app, host)
    app.decide_mobile_pair(host, {"pair_id": pair_id, "approve": True})
    app.poll_mobile_pair({"claim_token": claim["claim_token"]}, "phone")
    with pytest.raises(AccountError):
        app.poll_mobile_pair({"claim_token": claim["claim_token"]}, "phone")


def test_another_account_cannot_read_or_approve_a_pairing(app) -> None:
    host, other = _host(app), _host(app, "other@example.com")
    pair_id, _ = _claimed(app, host)
    for attempt in (
        lambda: app.mobile_pair_status(other, {"pair_id": pair_id}),
        lambda: app.decide_mobile_pair(other, {"pair_id": pair_id, "approve": True}),
    ):
        with pytest.raises(AccountError) as error:
            attempt()
        assert error.value.status == HTTPStatus.NOT_FOUND


def test_expired_pairing_is_gone(app) -> None:
    host = _host(app)
    pair_id, claim = _claimed(app, host)
    for entry in app._mobile_pairs.values():
        entry["expires_at"] = 0
    with pytest.raises(AccountError):
        app.mobile_pair_status(host, {"pair_id": pair_id})
    with pytest.raises(AccountError):
        app.poll_mobile_pair({"claim_token": claim["claim_token"]}, "phone")


@pytest.mark.parametrize(
    "path",
    [
        "/v1/models/credential",
        "/v1/auth/device-pair/issue",
        "/v1/auth/mobile-pair/issue",
        "/v1/auth/mobile-pair/decide",
        "/v1/auth/mobile-devices/revoke",
        "/v1/auth/profile",
        "/v1/search",
        "/v1/admin/me",
        "/v1/some/future/endpoint",
    ],
)
def test_phone_session_reaches_nothing_but_the_relay(app, path) -> None:
    session = _paired(app, _host(app))
    with pytest.raises(AccountError) as error:
        app.assert_session_may_reach(f"Bearer {session['access_token']}", path)
    assert error.value.code == "MOBILE_SESSION_FORBIDDEN"


def test_phone_session_keeps_its_relay_paths_and_its_kind(app) -> None:
    session = _paired(app, _host(app))
    bearer = f"Bearer {session['access_token']}"
    for path in ("/v1/auth/me", "/v1/auth/relay-credential", "/v1/auth/logout"):
        app.assert_session_may_reach(bearer, path)
    assert app.relay_credential(bearer)["client_kind"] == "mobile"

    refreshed = app.refresh({"refresh_token": session["refresh_token"]}, "phone")
    bearer = f"Bearer {refreshed['access_token']}"
    assert app.relay_credential(bearer)["client_kind"] == "mobile"
    with pytest.raises(AccountError):
        app.assert_session_may_reach(bearer, "/v1/models/credential")


def test_computer_session_is_unaffected(app) -> None:
    host = _host(app)
    app.assert_session_may_reach(host, "/v1/models/credential")
    assert app.relay_credential(host)["client_kind"] == "full"


def test_revoking_a_phone_ends_its_relay_access(app) -> None:
    host = _host(app)
    session = _paired(app, host)
    relay = app.relay_credential(f"Bearer {session['access_token']}")["relay_token"]
    assert app.relay_me(f"Bearer {relay}")["user"]

    devices = app.mobile_devices(host)["devices"]
    assert [device["name"] for device in devices] == ["Mate 50"]
    app.revoke_mobile_device(host, {"session_id": devices[0]["id"]})

    assert app.mobile_devices(host)["devices"] == []
    with pytest.raises(AccountError):
        app.relay_me(f"Bearer {relay}")


def test_revoke_cannot_touch_a_computer_session_or_another_account(app) -> None:
    host, other = _host(app), _host(app, "other@example.com")
    session = _paired(app, host)
    phone = app.mobile_devices(host)["devices"][0]["id"]

    with pytest.raises(AccountError):
        app.revoke_mobile_device(other, {"session_id": phone})
    with app.store._connect() as db:
        computer = db.execute("SELECT id FROM sessions WHERE client_kind = 'full' LIMIT 1").fetchone()["id"]
    with pytest.raises(AccountError):
        app.revoke_mobile_device(host, {"session_id": computer})
    assert app.relay_credential(f"Bearer {session['access_token']}")["relay_token"]
    assert app.me(host)["user"]


def _access(bearer: str) -> str:
    return bearer.split(" ", 1)[1]


def test_devices_show_trusted_phones_and_mark_the_one_asking(app) -> None:
    host = _host(app)
    session = _paired(app, host)

    from_phone = app.account_devices(f"Bearer {session['access_token']}")
    assert [(phone["name"], phone["current"]) for phone in from_phone["phones"]] == [("Mate 50", True)]
    assert app.account_devices(host)["phones"][0]["current"] is False
    assert from_phone["computers"] == [] and from_phone["history"] == []


def test_a_phone_may_read_devices_but_not_clear_them(app) -> None:
    session = _paired(app, _host(app))
    bearer = f"Bearer {session['access_token']}"
    app.assert_session_may_reach(bearer, "/v1/auth/devices")
    with pytest.raises(AccountError):
        app.assert_session_may_reach(bearer, "/v1/auth/devices/forget")


def test_a_removed_phone_moves_to_history_and_can_be_cleared(app) -> None:
    host = _host(app)
    _paired(app, host)
    phone = app.account_devices(host)["phones"][0]["id"]
    app.revoke_mobile_device(host, {"session_id": phone})

    devices = app.account_devices(host)
    assert devices["phones"] == []
    assert [entry["name"] for entry in devices["history"]] == ["Mate 50"]

    app.forget_device(host, {"kind": "phone", "id": phone})
    assert app.account_devices(host)["history"] == []


def test_unpairing_from_the_phone_lands_in_history_too(app) -> None:
    host = _host(app)
    session = _paired(app, host)
    app.logout({"refresh_token": session["refresh_token"]})
    assert [entry["name"] for entry in app.account_devices(host)["history"]] == ["Mate 50"]


def test_forget_never_ends_a_live_session_or_reaches_another_account(app) -> None:
    host, other = _host(app), _host(app, "other@example.com")
    session = _paired(app, host)
    phone = app.account_devices(host)["phones"][0]["id"]

    with pytest.raises(AccountError):
        app.forget_device(host, {"kind": "phone", "id": phone})
    assert app.relay_credential(f"Bearer {session['access_token']}")["relay_token"]

    app.revoke_mobile_device(host, {"session_id": phone})
    with pytest.raises(AccountError):
        app.forget_device(other, {"kind": "phone", "id": phone})
    with pytest.raises(AccountError):
        app.forget_device(host, {"kind": "anything", "id": phone})
    assert app.account_devices(host)["history"]


def test_relay_use_counts_as_phone_activity(app) -> None:
    host = _host(app)
    session = _paired(app, host)
    relay = app.relay_credential(f"Bearer {session['access_token']}")["relay_token"]
    phone = app.account_devices(host)["phones"][0]["id"]
    with app.store._connect() as db:
        db.execute("UPDATE sessions SET last_used_at = 1 WHERE id = ?", (phone,))
        db.execute("UPDATE relay_credentials SET last_used_at = 1 WHERE session_id = ?", (phone,))
    assert app.account_devices(host)["phones"][0]["online"] is False

    app.relay_me(f"Bearer {relay}")
    assert app.account_devices(host)["phones"][0]["online"] is True


def test_a_phone_that_lost_a_refresh_answer_stays_paired(app) -> None:
    host = _host(app)
    session = _paired(app, host)
    # The answer to this one never reached the phone: it still holds the token it sent.
    answered = app.refresh({"refresh_token": session["refresh_token"]}, "phone")

    again = app.refresh({"refresh_token": session["refresh_token"]}, "phone")
    assert app.relay_credential(f"Bearer {again['access_token']}")["client_kind"] == "mobile"
    assert app.account_devices(host)["phones"]
    # The token issued in between is not stranded either.
    assert app.refresh({"refresh_token": answered["refresh_token"]}, "phone")["access_token"]


def test_a_computer_session_keeps_strict_rotation(app) -> None:
    host = app.register({"email": "strict@example.com", "password": "correct-horse-battery-staple"}, "register")
    app.refresh({"refresh_token": host["refresh_token"]}, "host")
    with pytest.raises(AccountError):
        app.refresh({"refresh_token": host["refresh_token"]}, "host")


def test_two_refreshes_racing_do_not_revoke_anything(app) -> None:
    host = _host(app)
    session = _paired(app, host)
    first = app.refresh({"refresh_token": session["refresh_token"]}, "one")
    second = app.refresh({"refresh_token": session["refresh_token"]}, "two")
    assert first["refresh_token"] != second["refresh_token"]
    assert [phone["name"] for phone in app.account_devices(host)["phones"]] == ["Mate 50"]
    assert app.account_devices(host)["history"] == []


def test_a_revoked_phone_cannot_come_back_with_any_token(app) -> None:
    host = _host(app)
    session = _paired(app, host)
    rotated = app.refresh({"refresh_token": session["refresh_token"]}, "phone")
    app.revoke_mobile_device(host, {"session_id": app.account_devices(host)["phones"][0]["id"]})
    for token in (session["refresh_token"], rotated["refresh_token"]):
        with pytest.raises(AccountError):
            app.refresh({"refresh_token": token}, "phone")


def test_a_phone_stays_paired_for_a_year_and_renews_with_use(app) -> None:
    session = _paired(app, _host(app))
    with app.store._connect() as db:
        expires = db.execute("SELECT refresh_expires_at, created_at FROM sessions WHERE client_kind = 'mobile'").fetchone()
    assert expires["refresh_expires_at"] - expires["created_at"] >= 360 * 86400
    app.refresh({"refresh_token": session["refresh_token"]}, "phone")
    with app.store._connect() as db:
        renewed = db.execute("SELECT refresh_expires_at FROM sessions WHERE client_kind = 'mobile'").fetchone()
    assert renewed["refresh_expires_at"] >= expires["refresh_expires_at"]


def test_a_phone_that_pairs_again_replaces_itself(app) -> None:
    host = _host(app)
    _paired(app, host)
    _paired(app, host)
    devices = app.account_devices(host)
    assert len(devices["phones"]) == 1 and len(devices["history"]) == 1
