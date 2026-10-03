from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_BRIDGE = (ROOT / "desktop-react/src/webBridge.ts").read_text(encoding="utf-8")
WEB_GATE = (ROOT / "desktop-react/src/components/WebAppGate.tsx").read_text(encoding="utf-8")


def test_localhost_discovery_rebinds_stale_browser_socket() -> None:
    assert 'let socketDeviceId = "";' in WEB_BRIDGE
    assert 'const desiredDeviceId = selectedWebDeviceId();' in WEB_BRIDGE
    assert 'socket?.readyState === WebSocket.OPEN && socketDeviceId === desiredDeviceId' in WEB_BRIDGE
    assert 'new WebSocket(webSocketUrl(desiredDeviceId))' in WEB_BRIDGE
    assert 'const ws = await ensureSocket();' in WEB_BRIDGE
    assert 'ws.send(JSON.stringify({ type: "get_status" }));' in WEB_BRIDGE
    assert 'const existing = currentWebDeviceStatus();' not in WEB_BRIDGE


def test_local_device_binding_reloads_storage_instead_of_sticking_in_memory() -> None:
    assert 'const storedDeviceId = normalizeDeviceId(window.localStorage.getItem(LOCAL_DEVICE_STORAGE_KEY));' in WEB_BRIDGE
    assert 'if (storedDeviceId) localDeviceId = storedDeviceId;' in WEB_BRIDGE
    assert 'if (localDeviceId) return localDeviceId;' not in WEB_BRIDGE


def test_offline_host_is_not_misreported_as_updating() -> None:
    assert 'if (!compatibility.online)' in WEB_BRIDGE
    assert 'error.code = "HOST_OFFLINE";' in WEB_BRIDGE
    assert 'error.code = "HOST_UPDATE_REQUIRED";' in WEB_BRIDGE
    # The existing gate already maps every non-update connection failure to
    # offline, so HOST_OFFLINE cannot enter the updater state.
    assert 'error.code === "HOST_UPDATE_REQUIRED"' in WEB_GATE
    assert 'setTrackedHostState("offline");' in WEB_GATE
