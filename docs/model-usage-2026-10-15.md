# Model balance and quota queries

Every built-in group uses an authenticated `GET <gateway-base>/usage?model=...`
route. Authorization checks the current model entitlement on every request,
including cached and unsupported results. Desktop obtains the scoped Loom
model credential; only the gateway obtains upstream provider keys. Personal
connections query fixed, recognized provider origins with their own keys.

| Group | Data | Verification / limitation |
| --- | --- | --- |
| DeepSeek | Provider-account balances, preserving currency precision | Live production key returned valid balances. |
| MiniMax | Selected-model or general text-plan 5-hour and weekly windows | Live production key returned valid plan data. Unsupported model classes are excluded. |
| OpenCode Go | Rolling 5-hour, weekly and monthly plan windows, reset timestamps | Live production key returned all three windows. The API reports percent used; Loom shows `100 - used`. |
| Muxway Relay | New API-style token grant availability, when reported | Adapter tested with representative schema. Production `/api/usage/token/` and `/v1/models` both timed out; actual billing response remains unverified. No guessed money conversion. An unlimited token cap is not an unlimited wallet. |
| Intern Discovery | Explicit unavailable-via-API-key status | The provider's public console calls `/api/tokenplan/v1/credits/balance`. A production API key receives HTTP 401; it requires console login state. No cookie scraping. |
| Ant Ling | Explicit no-verified-query-interface status | Reviewed the official billing/FAQ documentation; no API-key balance interface verified. Do not infer a remaining balance from published free allowances. |

Built-in results explicitly identify the shared upstream account/key scope;
they are not separate allocations for each Loom user, model or device. Personal
results distinguish account-wide balance from per-key allowance. Unsupported,
unconfigured, HTTP rejection, network failure and malformed data do not become
zero, 100% remaining or unlimited. Unsupported personal providers can open
their known console; the shared built-in account's console link is not exposed.

Requests have bounded bodies, socket/request timeouts and no credential-bearing
redirects. Raw upstream responses, exceptions and keys are not forwarded to the
renderer. Successful transport reads are cached for 60 seconds and network/HTTP failures for 15;
cache keys include a credential fingerprint. Server reads are coalesced per
credential and parsed for the selected model. Refresh bypasses the cache.
Desktop UI is remounted on account changes and has no cross-account global
snapshot cache.

Sources reviewed:

- [OpenCode official usage endpoint](https://github.com/anomalyco/opencode/pull/16513)
- [MiniMax plan quota query](https://platform.minimaxi.com/subscribe/coding-plan)
- [Intern official console](https://discovery.intern-ai.org.cn/token-plan/home)
- [Ant Ling billing migration](https://developer.ant-ling.com/zh-CN/docs/getting-started/changelog/billing-upgrade/)
- [New API token usage endpoint](https://docs.newapi.pro/en/docs/api/management/token-management/usage-token-get)

Validation covers all six HTTP route groups, authorization before any read,
server-key isolation, provider-specific parsing, absent data, unlimited token
caps, cache/credential rotation, concurrent refresh and private-body omission.
Provider limitations above remain limitations, not completed live integrations.
