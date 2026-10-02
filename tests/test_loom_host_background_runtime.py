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
    assert "LOOM_HOST_RUNTIME_VERSION" in source
    auth = (ELECTRON / "webRelayAuth.ts").read_text(encoding="utf-8")
    assert "hostVersion: LOOM_HOST_RUNTIME_VERSION" in auth
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


def test_headless_updates_wait_for_idle_runtime_and_resume_headless() -> None:
    source = UPDATER.read_text(encoding="utf-8")
    main = MAIN.read_text(encoding="utf-8")
    assert "registerHeadlessUpdateGuard" in source
    assert "HEADLESS_INSTALL_RETRY_MS" in source
    assert "markHeadlessUpdateRestart();" in source
    assert "consumeHeadlessUpdateRestart" in source
    assert 'window.on("hide", () => { void maybeInstallHeadless(); });' in source
    assert "await rpc.assertRestartSafe();" in main
    assert "consumeHeadlessUpdateRestart()" in main


def test_web_copy_treats_desktop_as_optional_ui() -> None:
    source = PORTAL.read_text(encoding="utf-8")
    assert "Desktop is optional" in source
    assert "桌面端只是可选界面" in source
