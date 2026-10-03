import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_installer_registers_loom_launch_protocol() -> None:
    package = json.loads((ROOT / "desktop-react" / "package.json").read_text(encoding="utf-8"))
    protocols = package["build"].get("protocols", [])
    assert any("loom" in item.get("schemes", []) for item in protocols)
    # NSIS needs explicit Windows registry hooks; build.protocols alone is not
    # sufficient before the application has ever run.
    hook = ROOT / "desktop-react" / package["build"]["nsis"]["include"]
    nsis = hook.read_text(encoding="utf-8")
    assert "!macro customInstall" in nsis
    assert '"URL Protocol"' in nsis
    assert "APP_EXECUTABLE_FILENAME" in nsis
    assert '"Software\\Classes\\loom\\shell\\open\\command"' in nsis
    assert "SHChangeNotify" in nsis


def test_web_host_gate_covers_installed_but_not_running() -> None:
    source = (ROOT / "desktop-react" / "src" / "components" / "WebPortal.tsx").read_text(encoding="utf-8")
    setup = (ROOT / "desktop-react" / "src" / "components" / "HostSetupActions.tsx").read_text(encoding="utf-8")
    assert "loom://host/start" in setup
    assert "Already installed Loom?" in setup
    assert "HostSetupActions" in source
    assert "Loom Host not detected" in source
    assert "desktop window does not need to stay open" not in source
