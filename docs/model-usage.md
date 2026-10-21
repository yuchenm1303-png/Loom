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

Only explicitly supported official HTTPS origins receive credentials. Custom
relays without a verified adapter show unsupported. Built-in connections query
their authenticated Loom gateway; upstream keys remain server-side, and results
are clearly marked as shared upstream allowances, not personal allocations. Keys stay in
the Host and are never returned to the renderer. The in-memory cache is keyed by
a hash of the provider endpoint and credential, so editing a key cannot reuse a
different account's provider response. Redirects are rejected and response sizes
are capped at 64 KiB.

All seven built-in groups have an authenticated usage route. MiniMax exposes
model/general-plan windows, DeepSeek exposes currency balances, and OpenCode Go
exposes rolling/weekly/monthly windows. Go's current inference URL aliases use
the same Go usage endpoint, never the Console credential. Console, Intern and
Ant Ling explicitly report that no API-key query adapter is verified; missing
server credentials are reported separately. Relay uses `/api/usage/token`
(without a trailing slash) and accepts documented boolean success envelopes as
well as legacy numeric codes. A missing endpoint is unsupported, not zero quota.

Gateway authorization failures preserve allowlisted error codes without exposing
server messages. Expired Loom model credentials are refreshed once before another
read; permission denials are never retried as credential expiry. The popup names
the actual provider group even on failure. Transient refresh failures retain the
last successful reading for at most 15 minutes, visibly marked with its original
timestamp. Authentication/permission rejection immediately clears that display.

Live audit: MiniMax, DeepSeek and OpenCode Go returned valid usage; Relay's old
trailing-slash endpoint returned 404. No unsupported group is given invented
percentages, account wallet amounts, or unlimited allowances.

References:
- MiniMax: https://platform.minimax.io/docs/m-plan/faq
- DeepSeek: https://api-docs.deepseek.com/api/get-user-balance
- New API token usage: https://doc.newapi.pro/en/api/token-usage/

Validation:
- `npm run typecheck`
- `npm run build:electron` and `node --experimental-test-module-mocks --test electron/tests/*.test.mjs`
- `npm run build:renderer`
- With Vite and Playwright available, `node scripts/tests/model-usage-browser.mjs`
  (`LOOM_PLAYWRIGHT_MODULE`, `LOOM_CHROMIUM_PATH`, `LOOM_TEST_ORIGIN` can override the environment).
