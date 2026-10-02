from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "services" / "loom_web_gateway" / "app.py"
WEB_BRIDGE = ROOT / "desktop-react" / "src" / "webBridge.ts"
WEB_GATE = ROOT / "desktop-react" / "src" / "components" / "WebAppGate.tsx"
REMOTE_RELAY = ROOT / "desktop-react" / "electron" / "remoteRelay.ts"


def test_gateway_keeps_hosts_by_device_id_instead_of_eviction() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert "self.devices: dict[int, dict[str, DevicePeer]] = {}" in source
    assert "devices = hub.devices.setdefault(user_id, {})" in source
    assert "devices[device_id] = peer" in source
    assert 'reason="newer Loom Desktop connected"' not in source
    assert "newer Loom Host instance connected for this device" in source


def test_browser_routes_to_its_selected_device() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert 'selected_device_id: str = ""' in source
    assert "device = await hub.device_for_browser(peer)" in source
    assert 'websocket.query_params.get("device")' in source
    assert '"selectedDeviceId": browser.selected_device_id' in source
    assert '"devices": [dict(device.device) for device in devices]' in source
    assert '"code": "HOST_OFFLINE"' in source


def test_websocket_encodes_the_selected_device_routing_target() -> None:
    bridge = WEB_BRIDGE.read_text(encoding="utf-8")
    assert "const desiredDeviceId = selectedWebDeviceId();" in bridge
    assert 'target.searchParams.set("device", selectedDeviceId)' in bridge
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
    assert 'window.addEventListener("loom:web-device-status", onDeviceStatus)' in source


def test_web_gate_does_not_demote_an_online_host_during_background_discovery() -> None:
    source = WEB_GATE.read_text(encoding="utf-8")
    assert 'const hostStateRef = useRef<HostState>("idle")' in source
    assert "const connectPromiseRef = useRef<Promise<void> | null>(null)" in source
    assert 'if (!force && hostStateRef.current === "online") return;' in source
    assert 'if (hostStateRef.current !== "online") void connectCurrentHost();' in source
    assert 'setTrackedHostState("offline");' in source
    assert "void connectCurrentHost({ force: true });" in source


def test_web_gate_requires_explicit_user_entry_after_host_is_online() -> None:
    source = WEB_GATE.read_text(encoding="utf-8")
    assert "const [entered, setEntered] = useState(false);" in source
    assert 'if (hostStateRef.current === "online") {' in source
    assert "setEntered(true);" in source
    assert 'hostState !== "online" || !entered' in source
    assert "onEnter={enterOrRetry}" in source
    assert "setEntered(false);" in source


def test_web_gate_waits_for_protocol_update_and_reconnects() -> None:
    source = WEB_GATE.read_text(encoding="utf-8")
    assert '"updating"' in source
    assert "webHostNeedsProtocolUpdate" in source
    assert "ensureWebHostCompatibility" in source
    assert "ensureLocalLoomHostCompatibility" in source
    assert 'void connectCurrentHost({ force: true });' in source
