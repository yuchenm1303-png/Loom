from __future__ import annotations

from http import HTTPStatus

import pytest

from services.loom_account.server import (
    AccountApplication,
    AccountConfig,
    AccountError,
    AccountStore,
)


def _application(tmp_path) -> AccountApplication:
    return AccountApplication(AccountStore(AccountConfig(db_path=tmp_path / "accounts.db")))


def test_device_pair_ticket_is_one_time_and_creates_independent_session(tmp_path) -> None:
    application = _application(tmp_path)
    browser = application.register(
        {"email": "pairing@example.com", "password": "correct-horse-battery-staple"},
        "browser-register",
    )

    issued = application.issue_device_pair(
        f"Bearer {browser['access_token']}",
        "browser-pair",
    )
    ticket = str(issued["pairing_ticket"])
    assert ticket.startswith("loom_pair_")
    assert int(issued["expires_in"]) == 120

    host = application.exchange_device_pair(
        {"pairing_ticket": ticket},
        "host-exchange",
    )
    assert host["user"]["id"] == browser["user"]["id"]
    assert host["access_token"] != browser["access_token"]
    assert host["refresh_token"] != browser["refresh_token"]

    # Exchanging the ticket must not rotate or invalidate the browser session.
    browser_after_refresh = application.refresh(
        {"refresh_token": browser["refresh_token"]},
        "browser-refresh",
    )
    assert browser_after_refresh["user"]["id"] == browser["user"]["id"]

    # Tickets are consumed atomically and cannot be replayed by localhost or a
    # second Host process after the first successful exchange.
    with pytest.raises(AccountError) as replay:
        application.exchange_device_pair(
            {"pairing_ticket": ticket},
            "host-replay",
        )
    assert replay.value.status == HTTPStatus.UNAUTHORIZED
    assert replay.value.code == "INVALID_PAIRING_TICKET"
