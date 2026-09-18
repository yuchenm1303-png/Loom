from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "desktop-react" / "electron" / "main.ts"


def test_browser_bridge_does_not_move_the_runtime_home():
    """Pairing storage must not make existing conversations disappear."""

    source = MAIN.read_text(encoding="utf-8")
    assert 'LOOM_BROWSER_EXTENSION_TOKEN: ensureBrowserBridgeToken()' in source
    assert 'LOOM_HOME: app.getPath("userData")' not in source


def test_unpacked_extension_uses_a_stable_absolute_install_folder():
    source = MAIN.read_text(encoding="utf-8")
    # Resolved through the shared runtime home so the folder is the same one the
    # Python bridge uses, whichever build is running.
    assert 'path.join(loomRuntimeHome(), "browser", "current-tab-extension")' in source
    assert 'path.join(app.getPath("home"), ".loom")' in source
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


def test_one_pairing_token_is_shared_by_every_build():
    """Three tokens were in play at once and only one pairing could work.

    app.getPath("userData") is named after the build, so the packaged app and an
    unpackaged Electron run each kept their own token, while a Python server
    started from a checkout read a third from ~/.loom. The extension is paired
    with exactly one of them, so the other builds reported a broken extension
    that was in fact perfectly installed.
    """

    source = MAIN.read_text(encoding="utf-8")
    assert 'path.join(loomRuntimeHome(), "browser", "current-tab-bridge.token")' in source
    assert 'path.join(app.getPath("userData"), "browser", "current-tab-bridge.token")' not in source
    # And that home follows the same rule as _runtime_home() in the Python bridge,
    # LOOM_HOME included, or the two still diverge for anyone who sets it.
    home = source[source.index("function loomRuntimeHome()"):]
    home = home[: home.index("\n}\n")]
    assert "process.env.LOOM_HOME" in home
    assert 'path.join(app.getPath("home"), ".loom")' in home


def test_repair_refreshes_every_historical_install_location():
    """A build only knows its own userData name, so the names are listed.

    Chromium keys an unpacked extension by its absolute path: an install left in
    an older folder keeps loading that folder forever and cannot be upgraded from
    elsewhere, only kept in sync. Observed as a browser pinned to 0.1.9 while
    every source copy on disk had moved on.
    """

    source = MAIN.read_text(encoding="utf-8")
    body = source[source.index("function legacyBrowserExtensionTargets()"):]
    body = body[: body.index("\n}\n")]
    assert '"Electron", "browser", "current-tab-extension"' in body
    assert '"Loom", "browser", "current-tab-extension"' in body
    assert 'app.getPath("userData")' in body
    assert "new Set(" in body, "duplicate targets would be installed twice"


def test_setup_reports_the_extension_id_the_browser_will_show():
    """The browser names an unpacked extension by a hash of its path.

    Without it, "the version never updates" can only be diagnosed by reversing
    that hash by hand, which is exactly how this was found.
    """

    source = MAIN.read_text(encoding="utf-8")
    assert "function unpackedExtensionId(" in source
    assert 'Buffer.from(absolutePath, "utf16le")' in source
    assert "installedPaths:" in source
    assert "extensionId: unpackedExtensionId(" in source
