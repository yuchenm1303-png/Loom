from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "desktop-react" / "electron" / "main.ts"
WEB_BRIDGE = ROOT / "desktop-react" / "src" / "webBridge.ts"


def test_host_initialize_has_a_bounded_retry() -> None:
    source = MAIN.read_text(encoding="utf-8")
    assert "APP_SERVER_CONNECT_TIMEOUT_MS = 15_000" in source
    assert "initialize failed; restarting once" in source
    assert "return await this.initializeOnce()" in source
    assert "this.stopProcess(new Error(\"Loom App Server initialization timed out\"))" in source
    assert "private stopProcess(error: Error)" in source


def test_app_server_rpc_calls_cannot_stay_pending_forever() -> None:
    source = MAIN.read_text(encoding="utf-8")
    assert "APP_SERVER_CALL_TIMEOUT_MS = 120_000" in source
    assert "Loom App Server request timed out" in source
    assert "clearTimeout(pending.timer)" in source


def test_web_relay_invocations_have_timeout_cleanup() -> None:
    source = WEB_BRIDGE.read_text(encoding="utf-8")
    assert "CONNECT_INVOKE_TIMEOUT_MS = 40_000" in source
    assert "INVOKE_TIMEOUT_MS = 120_000" in source
    assert "Loom Host runtime did not become ready in time" in source
    assert "window.clearTimeout(call.timeout)" in source
