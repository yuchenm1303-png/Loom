"""Remaining shell contracts. Device routing is exercised through real WebSockets
in test_loom_web_gateway.py and through the Web bridge in web-bridge.test.mjs.
The old account-wide routing assertions described the audit defect.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = (ROOT / "services/loom_web_gateway/app.py").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "services/loom_web_gateway/Dockerfile").read_text(encoding="utf-8")
DESKTOP_MAIN = (ROOT / "desktop-react/electron/main.ts").read_text(encoding="utf-8")
REMOTE_RELAY = (ROOT / "desktop-react/electron/remoteRelay.ts").read_text(encoding="utf-8")
ENTRY = (ROOT / "desktop-react/electron/entry.ts").read_text(encoding="utf-8")
HOST_RUNTIME = (ROOT / "desktop-react/electron/hostProcess.ts").read_text(encoding="utf-8")
WEB_RELAY_AUTH = (ROOT / "desktop-react/electron/webRelayAuth.ts").read_text(encoding="utf-8")
UPDATER = (ROOT / "desktop-react/electron/updater.ts").read_text(encoding="utf-8")
HOST_UPDATER = (ROOT / "desktop-react/electron/hostRuntimeUpdater.ts").read_text(encoding="utf-8")
HOST_MODE = (ROOT / "desktop-react/electron/hostMode.ts").read_text(encoding="utf-8")
WEB_BRIDGE = (ROOT / "desktop-react/src/webBridge.ts").read_text(encoding="utf-8")
LOCAL_DISCOVERY = (ROOT / "desktop-react/src/localHostDiscovery.ts").read_text(encoding="utf-8")


def test_web_gateway_is_transport_not_agent_runtime() -> None:
    assert "CloudRuntimePool" not in GATEWAY
    assert "CloudModelConfig" not in GATEWAY
    assert "_run_cloud_invoke" not in GATEWAY
    assert "loom_app_server.py" not in DOCKERFILE
    assert "runtime.py" not in DOCKERFILE
    assert "COPY app ./app" not in DOCKERFILE


def test_desktop_window_is_not_the_host_lifetime() -> None:
    assert "app.requestSingleInstanceLock()" in ENTRY
    assert "--loom-background-host" in HOST_RUNTIME
    assert '--loom-host' in HOST_RUNTIME
    assert "app.setLoginItemSettings" in REMOTE_RELAY
    assert "new Tray(" in REMOTE_RELAY
    assert 'window.on("close"' not in REMOTE_RELAY
    assert "launchDesktop()" in REMOTE_RELAY
    assert "await prepareDesktopHost()" in DESKTOP_MAIN
    assert "await startHostTransport()" in DESKTOP_MAIN
    assert 'handleHostChannel("loom:connect"' in DESKTOP_MAIN
    assert 'ipcMain.handle("loom:disconnect", () => true)' in DESKTOP_MAIN
    assert "startWebRelay({ auth: webRelayAuthPayload, operations: desktopOperations })" in DESKTOP_MAIN


def test_web_system_actions_reach_local_host() -> None:
    assert 'readClipboardText: () => invoke("readClipboardText", [])' in WEB_BRIDGE
    assert 'writeClipboardText: (value) => invoke("writeClipboardText"' in WEB_BRIDGE
    assert 'pickDirectory: () => invoke("pickDirectory", [])' in WEB_BRIDGE
    assert 'frame.operation === "pickDirectory"' in REMOTE_RELAY
    assert "dialog.showOpenDialog" in REMOTE_RELAY


def test_web_host_negotiates_protocol_and_can_self_update() -> None:
    assert "LOOM_BOOTSTRAP_PROTOCOL_VERSION = 1" in WEB_RELAY_AUTH
    assert "hostProtocol: auth.hostProtocol" in REMOTE_RELAY
    assert "bootstrapProtocol: auth.bootstrapProtocol" in REMOTE_RELAY
    assert 'const LOCAL_UPDATE_PATH = "/loom/update"' in REMOTE_RELAY
    assert 'frame.operation === "hostUpdateEnsure"' in REMOTE_RELAY
    assert "ensureHostRuntimeUpdate(requiredProtocol)" in REMOTE_RELAY
    assert "hostRuntimeUpdateState()" in REMOTE_RELAY
    assert 'frame.operation === "bootstrapUpdateEnsure"' in REMOTE_RELAY
    assert "ensureBootstrapUpdate()" in REMOTE_RELAY
    assert "bootstrapAutoInstallRequested" in UPDATER
    assert "activateHostRuntime" in HOST_UPDATER
    assert "sha256(bytes) !== channel.sha256" in HOST_UPDATER
    assert "autoUpdater.quitAndInstall(false, true)" in UPDATER
    assert "LOOM_WEB_REQUIRED_HOST_PROTOCOL = 1" in LOCAL_DISCOVERY
    assert "ensureLocalLoomHostCompatibility" in LOCAL_DISCOVERY
    assert '"hostUpdateEnsure"' in WEB_BRIDGE
    assert 'error.code = "HOST_UPDATE_REQUIRED"' in WEB_BRIDGE
