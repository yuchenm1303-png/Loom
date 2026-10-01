from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = (ROOT / "services/loom_web_gateway/app.py").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "services/loom_web_gateway/Dockerfile").read_text(encoding="utf-8")
DESKTOP_MAIN = (ROOT / "desktop-react/electron/main.ts").read_text(encoding="utf-8")
REMOTE_RELAY = (ROOT / "desktop-react/electron/remoteRelay.ts").read_text(encoding="utf-8")
WEB_BRIDGE = (ROOT / "desktop-react/src/webBridge.ts").read_text(encoding="utf-8")


def test_web_gateway_is_transport_not_agent_runtime() -> None:
    assert "CloudRuntimePool" not in GATEWAY
    assert "CloudModelConfig" not in GATEWAY
    assert "_run_cloud_invoke" not in GATEWAY
    assert "loom_app_server.py" not in DOCKERFILE
    assert "runtime.py" not in DOCKERFILE
    assert "COPY app ./app" not in DOCKERFILE


def test_every_web_invoke_routes_to_local_host() -> None:
    assert "asyncio.create_task(_run_device_invoke(peer, request_id, operation, args))" in GATEWAY
    assert '"HOST_OFFLINE"' in GATEWAY
    assert 'elif kind == "notification":' in GATEWAY
    assert "await hub.broadcast_notification(user_id, payload)" in GATEWAY


def test_desktop_window_is_not_the_host_lifetime() -> None:
    assert "app.requestSingleInstanceLock()" in REMOTE_RELAY
    assert "--loom-background-host" in REMOTE_RELAY
    assert "app.setLoginItemSettings" in REMOTE_RELAY
    assert "new Tray(" in REMOTE_RELAY
    assert 'window.on("close"' in REMOTE_RELAY
    assert "event.preventDefault()" in REMOTE_RELAY
    assert "window.hide()" in REMOTE_RELAY
    assert "startWebRelay({ auth: webRelayAuthPayload, operations: desktopOperations })" in DESKTOP_MAIN


def test_web_system_actions_reach_local_host() -> None:
    assert 'readClipboardText: () => invoke("readClipboardText", [])' in WEB_BRIDGE
    assert 'writeClipboardText: (value) => invoke("writeClipboardText"' in WEB_BRIDGE
    assert 'pickDirectory: () => invoke("pickDirectory", [])' in WEB_BRIDGE
    assert 'frame.operation === "pickDirectory"' in REMOTE_RELAY
    assert "dialog.showOpenDialog" in REMOTE_RELAY
