from pathlib import Path


MAIN = Path(__file__).resolve().parents[1] / "desktop-react" / "electron" / "main.ts"


def test_browser_bridge_does_not_move_the_runtime_home():
    """Pairing storage must not make existing conversations disappear."""

    source = MAIN.read_text(encoding="utf-8")
    assert 'LOOM_BROWSER_EXTENSION_TOKEN: ensureBrowserBridgeToken()' in source
    assert 'LOOM_HOME: app.getPath("userData")' not in source
