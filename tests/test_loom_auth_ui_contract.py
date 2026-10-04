from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORTAL = ROOT / "desktop-react" / "src" / "components" / "WebPortal.tsx"
CSS = ROOT / "desktop-react" / "src" / "components" / "portal-modules.css"


def test_auth_ui_does_not_load_legacy_account_styles() -> None:
    source = PORTAL.read_text(encoding="utf-8")
    assert "portal-account-v1.css" not in source
    assert "portal-account-v2.css" not in source
    assert 'className="login-form loom-account-form"' not in source
    assert 'className="password-field"' not in source


def test_auth_fields_own_their_surface_and_autofill() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert ".loom-portal-page .loom-auth-v2 .loom-input-shell input" in css
    assert "input:-webkit-autofill" in css
    assert "background: transparent !important" in css
    assert ".loom-module-grid .loom-control.is-auth-mode" in css


def test_auth_submit_reads_real_dom_values() -> None:
    source = PORTAL.read_text(encoding="utf-8")
    assert 'new FormData(event.currentTarget)' in source
    assert 'name="password"' in source
    assert 'name="confirmPassword"' in source
    assert 'name="verificationCode"' in source
