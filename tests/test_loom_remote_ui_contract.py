"""Remaining shell contracts. Device routing is exercised through real WebSockets
in test_loom_web_gateway.py and through the Web bridge in web-bridge.test.mjs.
The old account-wide routing assertions described the audit defect.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "desktop-react" / "src" / "components" / "WebAppGate.tsx"


def test_web_ui_has_no_multi_host_selector() -> None:
    gate = GATE.read_text(encoding="utf-8")
    assert "selectWebDevice" not in gate
    assert "connectRemote" not in gate
    assert "RemoteSidebarEntry" not in gate
    assert "Remote devices" not in gate
