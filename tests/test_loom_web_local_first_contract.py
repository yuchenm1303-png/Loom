from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "services" / "loom_web_gateway" / "app.py"
WEB_BRIDGE = ROOT / "desktop-react" / "src" / "webBridge.ts"
WEB_GATE = ROOT / "desktop-react" / "src" / "components" / "WebAppGate.tsx"
REMOTE_RELAY = ROOT / "desktop-react" / "electron" / "remoteRelay.ts"


def test_gateway_tracks_multiple_hosts_per_account() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert "self.devices: dict[int, dict[str, DevicePeer]] = {}" in source
    assert "self.devices: dict[int, DevicePeer] = {}" not in source
    assert 'reason="newer Loom Host instance connected for this device"' in source
    assert 'reason="newer Loom Desktop connected"' not in source


def test_browser_never_falls_back_to_an_unselected_host() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert '"code": "HOST_NOT_SELECTED"' in source
    assert "hub.devices.get(peer.user_id, {}).get(peer.selected_device_id)" in source
    assert "browser.selected_device_id == peer.device_id" in source
    assert "peer.user_id == user_id and peer.selected_device_id == device_id" in source


def test_local_host_binding_is_created_by_the_current_desktop() -> None:
    relay = REMOTE_RELAY.read_text(encoding="utf-8")
    bridge = WEB_BRIDGE.read_text(encoding="utf-8")
    assert 'target.searchParams.set("local_device", auth.deviceId)' in relay
    assert 'const LOCAL_DEVICE_STORAGE_KEY = "loom.web.localDeviceId"' in bridge
    assert 'target.searchParams.set("device", deviceId)' in bridge
    assert 'type: "select_device"' in bridge


def test_web_gate_explains_local_first_behavior() -> None:
    source = WEB_GATE.read_text(encoding="utf-8")
    assert 'type HostState = "idle" | "checking" | "online" | "offline" | "unbound"' in source
    assert "Other computers are never selected automatically" in source
    assert "remote control belongs in Loom Remote" in source
