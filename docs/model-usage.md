# Model balance and quota

The circle to the right of the composer model selector opens provider-reported
quota or balance. MiniMax Token Plan connections show the 5-hour and weekly
remaining percentages and reset times. DeepSeek connections show each currency's
available, paid and granted balances. Money has no inferred percentage.

Queries run asynchronously in the desktop Host, independently of model switching
and the Python App Server. Existing results appear immediately; the Host caches
responses for 60 seconds and shares requests across models using the same provider
credential. The visible composer refreshes once per minute; the popup also offers
manual refresh. The first request depends on provider/network latency and has an
8-second deadline. Failed refreshes retain the last successful display, with a
warning and the original update timestamp.

Only the explicitly supported official HTTPS origins receive credentials. Custom
relays, other providers and Loom account gateway connections show unsupported;
upstream account-wide balances are not exposed to gateway clients. Keys stay in
the Host and are never returned to the renderer. The in-memory cache is keyed by
a hash of the provider endpoint and credential, so editing a key cannot reuse a
different account's provider response. Redirects are rejected and response sizes
are capped at 64 KiB.

References:
- MiniMax: https://platform.minimax.io/docs/m-plan/faq
- DeepSeek: https://api-docs.deepseek.com/api/get-user-balance

Validation:
- `npm run typecheck`
- `npm run build:electron` and `node --experimental-test-module-mocks --test electron/tests/*.test.mjs`
- `npm run build:renderer`
- With Vite and Playwright available, `node scripts/tests/model-usage-browser.mjs`
  (`LOOM_PLAYWRIGHT_MODULE`, `LOOM_CHROMIUM_PATH`, `LOOM_TEST_ORIGIN` can override the environment).
