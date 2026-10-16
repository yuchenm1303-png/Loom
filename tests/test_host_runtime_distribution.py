from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_runtime_selection_behavior() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required for runtime selection regression")
    if not (ROOT / "desktop-react/node_modules/typescript").is_dir():
        pytest.skip("Desktop TypeScript dependencies required for runtime regression")
    subprocess.run([node, str(ROOT / "tests/host_runtime_selection.cjs")], cwd=ROOT, check=True)


def test_extension_install_behavior() -> None:
    node = shutil.which("node")
    if not node or not (ROOT / "desktop-react/node_modules/typescript").is_dir():
        pytest.skip("Desktop Node/TypeScript dependencies required")
    subprocess.run([node, str(ROOT / "tests/browser_extension_install.cjs")], cwd=ROOT, check=True)


def test_runtime_and_ui_ship_in_the_complete_installer() -> None:
    assert not (ROOT / ".github/workflows/host-runtime-release.yml").exists()
    assert not (ROOT / ".github/workflows/application-runtime-release.yml").exists()
    assert not (ROOT / ".github/workflows/ui-runtime-release.yml").exists()
    runtime = (ROOT / "desktop-react/electron/hostRuntime.ts").read_text(encoding="utf-8")
    assert "managedHostRuntime" not in runtime
    assert "current.json" not in runtime
    main = (ROOT / "desktop-react/electron/main.ts").read_text(encoding="utf-8")
    assert "resolveHostPythonExecutable" in main
    assert "resolveHostBrowserExtensionRoot" in main
    assert "window.loadFile(builtIndex)" in main
    assert "loadUpdatedRenderer" not in main
    entry = (ROOT / "desktop-react/electron/entry.ts").read_text(encoding="utf-8")
    assert "selectApplication" not in entry
    assert "dist-electron/applicationEntry.js" in entry


def test_background_host_keeps_bootstrap_updates_armed() -> None:
    updater = (ROOT / "desktop-react/electron/updater.ts").read_text(encoding="utf-8")
    assert "bootstrapAutoInstallRequested" in updater
    assert "function runAutomaticCheck(): void" in updater
    assert "if (isHostProcess) void ensureBootstrapUpdate();" in updater
    assert "periodicTimer = setInterval(runAutomaticCheck, PERIODIC_CHECK_INTERVAL_MS);" in updater


def test_runtime_activation_synchronizes_browser_assets() -> None:
    main = (ROOT / "desktop-react/electron/main.ts").read_text(encoding="utf-8")
    assert "async function syncBrowserExtensionAssets" in main
    assert "await syncBrowserExtensionAssets();" in main
