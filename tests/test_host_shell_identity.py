from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_background_host_uses_single_loom_identity() -> None:
    relay = (ROOT / "desktop-react/electron/remoteRelay.ts").read_text(encoding="utf-8")
    main = (ROOT / "desktop-react/electron/main.ts").read_text(encoding="utf-8")

    assert 'window.setSkipTaskbar(false);' in relay
    assert 'window.setSkipTaskbar(true);' in relay
    assert 'Loom · running in the background' in relay
    assert 'label: "Quit Loom"' in relay
    assert 'Quit Loom Host' not in relay
    assert 'app.getFileIcon(process.execPath, { size: "normal" })' in relay
    assert 'skipTaskbar: isBackgroundHostLaunch(),' in main
