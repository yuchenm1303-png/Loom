# Cloudflare Turnstile — Loom Web authentication

Loom Web supports Cloudflare Turnstile for password sign-in, account creation
(including verified-email registration), and password-reset requests.

The independent **Web gateway**, not the browser or Desktop app, validates each
single-use Turnstile token with Cloudflare Siteverify. This keeps the public
sitekey separate from the secret and avoids breaking installed Windows clients.

## Production configuration

1. In Cloudflare → Turnstile → Add widget, create a managed widget for
   **loom.smirel.com**. Add any other approved Loom Web hostnames from
   \`LOOM_WEB_ORIGINS\` if they also host the same login flow.
2. Set these environment variables **on the Web gateway container only**:
   - \`LOOM_TURNSTILE_SITE_KEY\` — public site key.
   - \`LOOM_TURNSTILE_SECRET_KEY\` — private Siteverify secret.
3. Ensure your production Web gateway compose deployment passes both variables
   through to \`loom-web\` (the checked-in \`services/loom_web_gateway/docker-compose.yml\`
   supports them).
4. Recreate the \`loom-web\` container. Visit
   \`https://loom.smirel.com/api/auth/capabilities\` to confirm that
   \`turnstileSiteKey\` is populated. **Never publish the secret**.

Turnstile only activates when **both** keys are present. With either missing,
the capability advertises an empty key and the existing account flow remains
functional. Keep production credentials outside Git, and use separate keys
for local tests.

## Security notes

- Tokens are required by the Web gateway for \`/api/auth/login\`,
  \`/api/auth/register\`, \`/api/auth/register/start\`, and
  \`/api/auth/forgot-password\` when Turnstile is configured.
- Validation checks **success**, **action = loom_auth**, and the expected
  **hostname**. Expired, replayed, invalid, missing, or provider-unreachable
  tokens are rejected. Tokens are removed before forwarding credentials to the
  Account Service.
- The widget is renewed after an authentication attempt and automatically
  clears tokens when they expire.
- The Google and GitHub OAuth providers already run their own interactive
  authorization flows and are not gated with Turnstile here.
- **Scope:** This protects Loom's browser-to-Web-gateway password flows. The
  separately published native Account Service continues to serve native
  Desktop clients and does not require a web challenge; securing that API
  against direct bot calls needs an additional client-aware control or a
  coordinated native-client migration. Do not describe the gateway challenge
  as protection for all account endpoints.
- Keep existing IP/user rate limits and provider monitoring enabled.

## Testing

Frontend: \`cd desktop-react && npm run typecheck && node --test scripts/tests/portal-release.test.mjs\`

Backend: \`pytest tests/test_loom_web_gateway.py -q\`

Cloudflare test site keys can be used in staging with matching **test** secrets,
never in production. Real production keys must be hostname-restricted.
