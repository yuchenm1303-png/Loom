from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "services" / "loom_web_gateway" / "app.py"
WEB_BRIDGE = ROOT / "desktop-react" / "src" / "webBridge.ts"
WEB_GATE = ROOT / "desktop-react" / "src" / "components" / "WebAppGate.tsx"
REMOTE_RELAY = ROOT / "desktop-react" / "electron" / "remoteRelay.ts"


def test_gateway_has_one_current_host_per_account() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert "self.devices: dict[int, DevicePeer] = {}" in source
    assert "self.devices: dict[int, dict[str, DevicePeer]] = {}" not in source
    assert 'reason="newer Loom Desktop connected"' in source
    assert "newer Loom Host instance connected for this device" not in source


def test_browser_routes_by_account_without_device_selection() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert "device = hub.devices.get(peer.user_id)" in source
    assert '"code": "HOST_OFFLINE"' in source
    assert '"code": "HOST_NOT_SELECTED"' not in source
    assert "selected_device_id" not in source
    assert 'kind == "select_device"' not in source


def test_websocket_does_not_encode_a_device_routing_target() -> None:
    bridge = WEB_BRIDGE.read_text(encoding="utf-8")
    assert 'target.searchParams.delete("device")' in bridge
    assert 'target.searchParams.set("device"' not in bridge
    assert 'type: "select_device"' not in bridge


def test_loopback_discovery_only_bootstraps_local_pairing() -> None:
    relay = REMOTE_RELAY.read_text(encoding="utf-8")
    assert 'const LOCAL_PAIR_PATH = "/loom/pair"' in relay
    assert 'new LoomAccountClient().pairDevice(pairingTicket)' in relay
    assert 'target.searchParams.set("local_device", identity.deviceId)' in relay
    assert "127.0.0.1" in relay


def test_web_gate_automatically_follows_current_account_host() -> None:
    source = WEB_GATE.read_text(encoding="utf-8")
    assert "await window.loom.connect();" in source
    assert "selectWebDevice" not in source
    assert "connectRemote" not in source
    assert "Remote devices" not in source
    assert "one current Host" in source
