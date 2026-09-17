from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extensions" / "browser-current-tab"


def test_terminal_turns_end_the_browser_hud_without_closing_the_browser() -> None:
    policy = (ROOT / "app" / "app_server_browser_policy.py").read_text(encoding="utf-8")

    for kind in (
        "TURN_COMPLETED",
        "TURN_FAILED",
        "TURN_CANCELLED",
        "TURN_INTERRUPTED",
        "LIMIT_REACHED",
    ):
        assert f"AgentEventKind.{kind}" in policy

    assert 'bridge.call("hud_end"' in policy
    assert "timeout=2.0" in policy
    # Ending the visual turn must not destroy the persistent browser session,
    # browser_id, login state, or the user's tabs.
    assert "browser_sessions.close_owner" not in policy
    assert "browser_sessions.close(" not in policy


def test_hud_end_is_consumed_by_the_extension_worker_and_fades_every_hud_layer() -> None:
    worker = (EXT / "bridge-worker.js").read_text(encoding="utf-8")

    assert "command.action !== 'hud_end'" in worker
    assert "hideBrowserHudEverywhere" in worker
    assert "loom-browser-page-hud-root" in worker
    assert "loom-browser-hud-root-v2" in worker
    assert "classList.remove('live')" in worker
    assert "__loomBrowserHudRuntimeV2" in worker
    assert "/browser-extension/v1/result" in worker
    assert "await import('./background.js')" in worker


def test_turn_end_hud_fix_ships_as_a_new_extension_version() -> None:
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    version = tuple(int(part) for part in str(manifest["version"]).split("."))
    assert version >= (0, 1, 12)
