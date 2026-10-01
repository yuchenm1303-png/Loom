from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_loom_web_uses_original_smirel_brand_and_portal_skeleton():
    gate = (ROOT / "desktop-react/src/components/WebAppGate.tsx").read_text(encoding="utf-8")
    logo = (ROOT / "desktop-react/public/smirel-logo.svg").read_text(encoding="utf-8")
    mark = (ROOT / "desktop-react/public/smirel-mark.svg").read_text(encoding="utf-8")
    assert "function SmirelShell" in gate
    assert "smirel-web-shell" in gate
    assert "SMIREL · LOOM WEB" in gate
    assert "LOOM ACCOUNT" in gate
    assert "smirel-logo" in logo.lower()
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
