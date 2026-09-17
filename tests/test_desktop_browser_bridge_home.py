from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "desktop-react" / "electron" / "main.ts"


def test_browser_bridge_does_not_move_the_runtime_home():
    """Pairing storage must not make existing conversations disappear."""

    source = MAIN.read_text(encoding="utf-8")
    assert 'LOOM_BROWSER_EXTENSION_TOKEN: ensureBrowserBridgeToken()' in source
    assert 'LOOM_HOME: app.getPath("userData")' not in source


def test_unpacked_extension_uses_a_stable_absolute_install_folder():
    source = MAIN.read_text(encoding="utf-8")
    assert 'path.join(app.getPath("home"), ".loom", "browser", "current-tab-extension")' in source
    assert 'shell.openPath(target)' in source
    assert 'clipboard.writeText(target)' in source


def test_existing_extension_updates_without_reopening_browser_setup():
    source = MAIN.read_text(encoding="utf-8")
    assert "extensionConnected = false" in source
    assert 'path.join(installTarget, "extension-update.json")' in source
    assert "automaticUpdateRequested: extensionConnected" in source
    assert "if (!extensionConnected && executable)" in source


def test_update_migrates_an_extension_loaded_from_the_old_electron_directory():
    source = MAIN.read_text(encoding="utf-8")
    assert "function legacyBrowserExtensionTargets()" in source
    assert 'path.join(app.getPath("userData"), "browser", "current-tab-extension")' in source
    assert "const installTargets = [target, ...legacyBrowserExtensionTargets()]" in source
    assert "migratedLegacyInstalls:" in source
