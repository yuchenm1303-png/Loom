from __future__ import annotations

import json
import os
import stat

import pytest

from app.agent_runtime.journal import atomic_json, recover


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not a Windows ACL contract")
def test_atomic_json_uses_user_only_permissions(tmp_path) -> None:
    target = tmp_path / ".pending-commit.json"

    atomic_json(target, {"secret": "provider-private reasoning"})

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text(encoding="utf-8"))["secret"] == "provider-private reasoning"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not a Windows ACL contract")
def test_crash_recovery_keeps_session_snapshot_private(tmp_path) -> None:
    pending = tmp_path / ".pending-commit.json"
    atomic_json(
        pending,
        {
            "session": {"_provider_reasoning_content": "hidden"},
            "event": {"event_id": "event-1"},
        },
    )

    recover(tmp_path)

    session_path = tmp_path / "session.json"
    assert session_path.is_file()
    assert stat.S_IMODE(session_path.stat().st_mode) == 0o600
    assert json.loads(session_path.read_text(encoding="utf-8"))["_provider_reasoning_content"] == "hidden"
    assert not pending.exists()
