from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_host_runtime_has_its_own_release_channel() -> None:
    workflow = (ROOT / ".github/workflows/host-runtime-release.yml").read_text(encoding="utf-8")
    package = (ROOT / "desktop-react/scripts/package-host-runtime.mjs").read_text(encoding="utf-8")
    config = (ROOT / "host-runtime/runtime.json").read_text(encoding="utf-8")
    assert "--prerelease" in workflow
    assert "host-v$version" in workflow
    assert "host-runtime/stable.json" in workflow
    assert "Loom-Host-Runtime-$version-win-x64.zip" in workflow
    assert "Compress-Archive" in package
    assert '"minBootstrapVersion"' in config


def test_host_runtime_is_selected_independently_from_desktop() -> None:
    runtime = (ROOT / "desktop-react/electron/hostRuntime.ts").read_text(encoding="utf-8")
    updater = (ROOT / "desktop-react/electron/hostRuntimeUpdater.ts").read_text(encoding="utf-8")
    main = (ROOT / "desktop-react/electron/main.ts").read_text(encoding="utf-8")
    models = (ROOT / "desktop-react/electron/modelManager.ts").read_text(encoding="utf-8")
    assert 'path.join(app.getPath("userData"), "host-runtime")' in runtime
    assert "current.json" in runtime
    assert "resolveHostPythonExecutable" in main
    assert "resolveHostBrowserExtensionRoot" in main
    assert "resolveHostPythonExecutable(this.repoRoot)" in models
    assert "Runtime activation rolled back" in updater
    assert "minBootstrapVersion" in updater


def test_background_host_does_not_poll_full_desktop_updates() -> None:
    updater = (ROOT / "desktop-react/electron/updater.ts").read_text(encoding="utf-8")
    assert "bootstrapAutoInstallRequested" in updater
    assert "if (hasVisibleWindow()) startAutomaticChecks();" in updater
    assert "ensureBootstrapUpdate" in updater
