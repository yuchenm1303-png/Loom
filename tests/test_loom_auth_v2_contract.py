from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = ROOT / "services" / "loom_account" / "server.py"
GATEWAY = ROOT / "services" / "loom_web_gateway" / "app.py"
PORTAL = ROOT / "desktop-react" / "src" / "components" / "WebPortal.tsx"
BRIDGE = ROOT / "desktop-react" / "src" / "webBridge.ts"
USE_ACCOUNT = ROOT / "desktop-react" / "src" / "state" / "useAccount.ts"


def test_account_service_has_verified_email_and_recovery_flows() -> None:
    source = ACCOUNT.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS auth_identities" in source
    assert "CREATE TABLE IF NOT EXISTS email_challenges" in source
    assert "email_verified_at" in source
    assert '"/v1/auth/register/start"' in source
    assert '"/v1/auth/verify-email"' in source
    assert '"/v1/auth/forgot-password"' in source
    assert '"/v1/auth/reset-password"' in source
    assert "UPDATE sessions SET revoked_at" in source
    assert "hmac.compare_digest(actual, expected)" in source


def test_account_service_has_google_and_github_oauth_with_one_time_exchange() -> None:
    source = ACCOUNT.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS oauth_states" in source
    assert "CREATE TABLE IF NOT EXISTS oauth_exchange_codes" in source
    assert "https://accounts.google.com/o/oauth2/v2/auth" in source
    assert "https://github.com/login/oauth/authorize" in source
    assert "consume_oauth_exchange" in source
    assert "OAUTH_CODE_INVALID" in source


def test_gateway_keeps_browser_tokens_http_only_for_all_new_auth_flows() -> None:
    source = GATEWAY.read_text(encoding="utf-8")
    assert 'httponly=True' in source
    assert '@app.post("/api/auth/verify-email")' in source
    assert '@app.post("/api/auth/reset-password")' in source
    assert '@app.post("/api/auth/oauth/exchange")' in source
    assert '@app.get("/api/auth/oauth/start/{provider}")' in source
    assert '_set_session_cookies(response, payload)' in source


def test_portal_exposes_rich_auth_without_replacing_local_first_gate() -> None:
    portal = PORTAL.read_text(encoding="utf-8")
    bridge = BRIDGE.read_text(encoding="utf-8")
    hook = USE_ACCOUNT.read_text(encoding="utf-8")
    assert "Continue with Google" in portal
    assert "Continue with GitHub" in portal
    assert "Verify your email" in portal
    assert "Forgot password?" in portal
    assert "Set a new password" in portal
    assert "loom_oauth_code" in portal
    assert "accountRegisterStart" in bridge
    assert "accountVerifyEmail" in bridge
    assert "accountOAuthExchange" in bridge
    assert "refreshCapabilities" in hook
    assert "account.capabilities.emailVerification" in portal
