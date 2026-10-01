from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_loom_web_routes_the_gate_through_the_functional_glass_portal():
    gate = (ROOT / "desktop-react/src/components/WebAppGate.tsx").read_text(encoding="utf-8")
    portal = (ROOT / "desktop-react/src/components/WebPortal.tsx").read_text(encoding="utf-8")
    mark = (ROOT / "desktop-react/public/smirel-mark.svg").read_text(encoding="utf-8")
    assert 'import { WebPortal, type PortalHostState } from "./WebPortal"' in gate
    assert "<WebPortal" in gate
    assert "function SmirelShell" not in gate
    assert "AccountDialog" not in gate
    assert 'import "./portal-base.css"' in portal
    assert 'import "./portal-modules.css"' in portal
    assert 'import "./portal-host-card.css"' in portal
    assert "loom-module-grid" in portal
    assert "loom-intro-module cards fade" in portal
    assert "loom-sidebar-module cards fade" in portal
    assert "loom-host-module loom-host-refined" in portal
    assert "loom-account-module loom-account-refined" in portal
    assert "Your Loom stays" in portal
    assert "on your computer." in portal
    assert "Download for Windows" in portal
    assert "Continue with Loom" in portal
    assert (ROOT / "desktop-react/public/smirel-logo.png").exists()
    assert "Smirel API" in mark


def test_loom_web_loads_the_canonical_listing_studio_glass_assets():
    portal = (ROOT / "desktop-react/src/components/WebPortal.tsx").read_text(encoding="utf-8")
    glue = (ROOT / "desktop-react/src/components/web-smirel.css").read_text(encoding="utf-8")
    required = [
        "styles-v3.css",
        "cosmic-bright-v1.css",
        "portal-polish-v1.css",
        "portal-polish-v2.css",
        "portal-account-v1.css",
        "portal-account-v2.css",
        "layout-visual-restore-v1.css",
        "cursor-reference-source-v1.css",
        "session-boot-v1.css",
        "release-history-v1.css",
        "beach-wallpaper-v1.css",
        "wallpaper-ready-v1.css",
    ]
    for name in required:
        assert f"https://smirel.com/download/{name}" in portal
    assert "https://smirel.com/download/wallpaper-beach-blue-v1-original.png" in portal
    assert ".smirel-web-shell" not in glue
    assert "background: #06080d" not in glue
