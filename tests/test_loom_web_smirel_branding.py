from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_loom_web_gate_uses_original_smirel_brand_asset():
    gate = (ROOT / "desktop-react/src/components/WebAppGate.tsx").read_text(encoding="utf-8")
    logo = (ROOT / "desktop-react/public/smirel-logo.svg").read_text(encoding="utf-8")

    assert 'const SMIREL_LOGO = "/smirel-logo.svg"' in gate
    assert "SMIREL · LOOM WEB" in gate
    assert '<img src={SMIREL_LOGO} alt="Smirel" />' in gate
    assert "smirel-logo-title" in logo


def test_smirel_web_shell_styles_are_scoped_to_web_gate():
    gate = (ROOT / "desktop-react/src/components/WebAppGate.tsx").read_text(encoding="utf-8")
    css = (ROOT / "desktop-react/src/components/web-smirel.css").read_text(encoding="utf-8")

    assert 'import "./web-smirel.css"' in gate
    assert ".smirel-web-shell" in css
    assert ".smirel-web-shell .loom-account-dialog" in css
    assert ".smirel-web-shell .loom-account-close" in css
    assert "background: url(/smirel-logo.svg)" in css
    assert 'html[data-loom-web="true"] body' in css
