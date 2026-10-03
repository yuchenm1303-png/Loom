from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_background_host_uses_single_loom_identity() -> None:
    relay = (ROOT / "desktop-react/electron/remoteRelay.ts").read_text(encoding="utf-8")
    main = (ROOT / "desktop-react/electron/main.ts").read_text(encoding="utf-8")

    assert 'Loom · running in the background' in relay
    assert 'label: "Quit Loom"' in relay
    assert '"loom-icon.png"' in relay
    assert 'nativeImage.createFromPath(iconPath)' in relay
    assert 'app.setAppUserModelId("com.loom.agent")' in main
    assert 'skipTaskbar: false,' in main
