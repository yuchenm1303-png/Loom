from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "desktop-react" / "src" / "webBridge.ts"
GATE = ROOT / "desktop-react" / "src" / "components" / "WebAppGate.tsx"
REMOTE = ROOT / "desktop-react" / "src" / "components" / "RemoteDevicesPanel.tsx"
ENTRY = ROOT / "desktop-react" / "src" / "components" / "RemoteSidebarEntry.tsx"
GATEWAY = ROOT / "services" / "loom_web_gateway" / "app.py"


def test_remote_target_is_explicit_and_session_scoped() -> None:
    source = BRIDGE.read_text(encoding="utf-8")
    assert 'const REMOTE_DEVICE_SESSION_KEY = "loom.web.remoteDeviceId"' in source
    assert "window.sessionStorage.setItem(REMOTE_DEVICE_SESSION_KEY, target)" in source
    assert "window.localStorage.setItem(REMOTE_DEVICE_SESSION_KEY" not in source
    assert 'export function webExecutionMode(): "local" | "remote"' in source
    assert "return remote || captureLocalDeviceBinding()" in source


def test_desktop_local_open_always_wins_over_remote_session() -> None:
    source = BRIDGE.read_text(encoding="utf-8")
    local_capture = source[source.index("function captureLocalDeviceBinding"):source.index("function remoteWebDeviceId")]
    assert "window.sessionStorage.removeItem(REMOTE_DEVICE_SESSION_KEY)" in local_capture


def test_remote_ui_keeps_local_first_product_shape() -> None:
    panel = REMOTE.read_text(encoding="utf-8")
    entry = ENTRY.read_text(encoding="utf-8")
    gate = GATE.read_text(encoding="utf-8")
    assert "This computer" in panel
    assert "Other computers" in panel
    assert "Back to this computer" in panel
    assert "RemoteSidebarEntry" in gate
    assert 'loom:web-open-remote' in entry
    assert "Remote devices" in gate


def test_gateway_still_forbids_implicit_device_fallback() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert '"code": "HOST_NOT_SELECTED"' in source
    assert "hub.devices.get(peer.user_id, {}).get(peer.selected_device_id)" in source
    assert "browser.selected_device_id == peer.device_id" in source
