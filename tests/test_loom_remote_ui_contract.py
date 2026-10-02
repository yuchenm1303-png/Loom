from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "desktop-react" / "src" / "webBridge.ts"
GATE = ROOT / "desktop-react" / "src" / "components" / "WebAppGate.tsx"
GATEWAY = ROOT / "services" / "loom_web_gateway" / "app.py"


def test_web_ui_has_no_multi_host_selector() -> None:
    gate = GATE.read_text(encoding="utf-8")
    assert "selectWebDevice" not in gate
    assert "connectRemote" not in gate
    assert "RemoteSidebarEntry" not in gate
    assert "Remote devices" not in gate


def test_gateway_routes_each_browser_to_one_selected_host() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert "self.devices: dict[int, dict[str, DevicePeer]] = {}" in source
    assert "selected_device_id" in source
    assert "device = await hub.device_for_browser(peer)" in source
    assert 'reason="newer Loom Desktop connected"' not in source


def test_web_bridge_sends_its_device_target_during_connect() -> None:
    source = BRIDGE.read_text(encoding="utf-8")
    assert "const desiredDeviceId = selectedWebDeviceId();" in source
    assert 'target.searchParams.set("device", selectedDeviceId)' in source
    assert 'type: "select_device"' not in source
