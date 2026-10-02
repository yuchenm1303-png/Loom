from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ELECTRON = ROOT / "desktop-react" / "electron"
MAIN = ELECTRON / "main.ts"
RELAY = ELECTRON / "remoteRelay.ts"
UPDATER = ELECTRON / "updater.ts"
HOST_MODE = ELECTRON / "hostMode.ts"
PORTAL = ROOT / "desktop-react" / "src" / "components" / "WebPortal.tsx"


def test_host_has_independent_runtime_identity_and_background_mode() -> None:
    source = HOST_MODE.read_text(encoding="utf-8")
    assert "BACKGROUND_HOST_ARG" in source
    assert "--loom-background-host" in source
    assert "LOOM_HOST_RUNTIME_VERSION" not in source
    runtime = (ELECTRON / "hostRuntime.ts").read_text(encoding="utf-8")
    updater = (ELECTRON / "hostRuntimeUpdater.ts").read_text(encoding="utf-8")
    auth = (ELECTRON / "webRelayAuth.ts").read_text(encoding="utf-8")
    assert "currentHostRuntimeVersion()" in auth
    assert "currentHostRuntimeProtocol()" in auth
    assert "LOOM_BOOTSTRAP_PROTOCOL_VERSION" in auth
    assert "host-runtime" in runtime
    assert "activateHostRuntime" in runtime
    assert "ensureHostRuntimeUpdate" in updater
    assert "hostMode: loomHostLaunchMode()" in auth


def test_background_host_does_not_boot_the_desktop_renderer() -> None:
    source = MAIN.read_text(encoding="utf-8")
    assert 'if (resumeHeadlessHost) setLoomHostLaunchMode("background");' in source
    assert "if (!isBackgroundHostLaunch()) ensureDesktopUi();" in source
    assert "registerDesktopWindowFactory(ensureDesktopUi);" in source
    assert "requestDesktopWindow();" in source
    window_closed = source.split('app.on("window-all-closed"', 1)[1].split('app.on("before-quit"', 1)[0]
    assert "app.quit()" not in window_closed


def test_host_starts_at_login_and_can_lazily_open_desktop() -> None:
    source = RELAY.read_text(encoding="utf-8")
    assert "app.setLoginItemSettings" in source
    assert "args: [BACKGROUND_HOST_ARG]" in source
    assert "registerDesktopWindowFactory" in source
    assert "desktopWindowFactory();" in source
    assert "Quit Loom Host" in source


def test_host_runtime_updates_independently_and_bootstrap_update_is_rare() -> None:
    source = UPDATER.read_text(encoding="utf-8")
    runtime_updater = (ELECTRON / "hostRuntimeUpdater.ts").read_text(encoding="utf-8")
    main = MAIN.read_text(encoding="utf-8")
    assert "bootstrapAutoInstallRequested" in source
    assert "ensureBootstrapUpdate" in source
    assert "registerHostRuntimeUpdateHooks" in runtime_updater
    assert "checksum verification failed" in runtime_updater
    assert "Runtime activation rolled back" in runtime_updater
    assert "await rpc.assertRestartSafe();" in main
    assert "resolveHostPythonExecutable" in main
    assert "resolveHostBrowserExtensionRoot" in main


def test_web_copy_treats_desktop_as_optional_ui() -> None:
    source = PORTAL.read_text(encoding="utf-8")
    assert "Desktop is optional" in source
    assert "桌面端只是可选界面" in source
