import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_installer_registers_loom_launch_protocol() -> None:
    package = json.loads((ROOT / "desktop-react" / "package.json").read_text(encoding="utf-8"))
    protocols = package["build"].get("protocols", [])
    assert any("loom" in item.get("schemes", []) for item in protocols)


def test_web_host_gate_covers_installed_but_not_running() -> None:
    source = (ROOT / "desktop-react" / "src" / "components" / "WebPortal.tsx").read_text(encoding="utf-8")
    setup = (ROOT / "desktop-react" / "src" / "components" / "HostSetupActions.tsx").read_text(encoding="utf-8")
    assert "loom://host/start" in setup
    assert "Already installed Loom?" in setup
    assert "HostSetupActions" in source
    assert "Loom Host not detected" in source
    assert "desktop window does not need to stay open" not in source
