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


def test_gateway_enforces_single_current_host_routing() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert "self.devices: dict[int, DevicePeer] = {}" in source
    assert 'reason="newer Loom Desktop connected"' in source
    assert "selected_device_id" not in source
    assert '"HOST_NOT_SELECTED"' not in source


def test_legacy_bridge_selector_cannot_change_gateway_target() -> None:
    source = BRIDGE.read_text(encoding="utf-8")
    assert 'target.searchParams.set("device"' not in source
    assert 'type: "select_device"' not in source
    assert "await ensureSocket();" in source
