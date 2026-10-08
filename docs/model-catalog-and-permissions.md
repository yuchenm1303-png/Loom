# Server-owned model catalog and permissions

The model policy service owns the built-in model directory. Desktop model
discovery supplies connection capabilities, but cannot add models to the policy
database or override directory availability. Saved personal connections are
outside built-in policy.

## Data flow

1. `CatalogSynchronizer` discovers each configured provider on service startup
   and every 300 seconds. Providers refresh independently. An administrator can
   request a refresh with **同步模型目录** or `POST /v1/admin/catalog/refresh`.
2. A complete, valid provider response atomically updates that provider's
   persistent catalog. New IDs receive the existing default policy. Existing
   global, account and access-group rules are never overwritten by discovery.
3. Models absent from a successful listing become unavailable. Their catalog
   rows and all permission rules remain in SQLite. Reappearing IDs recover their
   previous rules. A rename is a new identity; permissions are not silently
   transferred between unrelated model IDs.
4. Failed, malformed, oversized, explicitly paginated or empty responses preserve the last successful
   catalog. Without discovery credentials, the bundled directory remains
   available. Refresh status and sanitized errors appear in the admin console.
5. `/v1/access` returns directory, effective decisions, schema version and
   content revisions together. Desktop replaces its built-in picker directory
   with these rows; new models receive local connection metadata in two batch
   bridge requests. The Ant Ling gateway uses the same directory for listing
   and authorization, including new models.
6. `/v1/check` resolves an exact selection with the same policy engine. Unknown
   and retired built-in selections are denied with source `catalog`; actual
   administrator denials retain their policy source. Policy outages disable
   built-in selections in the picker without disabling personal connections.

## Deployment

Rebuild the policy service, Ant Ling model gateway and desktop client together.
The account deployment now loads its existing private `.env.models` into the
policy container as well as the gateway. Additional directory-discovery keys
belong in the policy-only `.env.catalog`, so other providers' credentials are
not unnecessarily passed to the Ant Ling gateway. Keep all credentials in that server
environment, never in Git. Configure discovery credentials for every provider
whose complete directory must update automatically:

| Group | Credential | Optional base URL |
| --- | --- | --- |
| MiniMax | `MINIMAX_API_KEY` | `LOOM_MINIMAX_BASE_URL` |
| DeepSeek | `DEEPSEEK_API_KEY` | `LOOM_DEEPSEEK_BASE_URL` |
| Ant Ling | `LOOM_ANT_LING_API_KEY` | `LOOM_ANT_LING_BASE_URL` |
| OpenCode Go | `OPENCODE_GO_API_KEY` | `LOOM_OPENCODE_GO_BASE_URL` |
| Relay | `LOOM_RELAY_API_KEY` | `LOOM_RELAY_BASE_URL` |

Base URLs are API roots; discovery appends `/models`. Each provider listing is
the authoritative directory for that configured server credential. Use a
credential with the desired complete catalog, rather than an account restricted
to a small subset. Upstream APIs that do not expose a usable model listing can
use a trusted server manifest instead.

Endpoints reporting further pages are deliberately not treated as a complete
directory; use a complete manifest until a provider-specific pagination adapter
is available. This prevents accidental retirement from a partial page.

`LOOM_MODEL_CATALOG_MANIFEST` points to a JSON file accessible inside the policy
container (for example, a read-only bind mount). Keys are provider group IDs,
and values are OpenAI-shaped model rows. Specified groups use the manifest;
other groups continue provider discovery:

```json
{
  "ant-ling": [
    {"id": "Ling-3.0-flash", "name": "Ling 3.0 Flash"},
    {"id": "Ling-3.0-flash-VL", "name": "Ling 3.0 Flash VL"}
  ]
}
```

The example is illustrative; a manifest must contain the entire intended
directory for each specified group. Empty manifests for individual groups are
treated as an error, so stopping a provider should use its global group switch.
`LOOM_MODEL_CATALOG_REFRESH_SECONDS` changes discovery cadence (minimum 30
seconds). No credential is included in access responses or stored refresh errors.

## Compatibility and propagation bounds

- Old clients can still use `GET /access`, `POST /access` with requested model
  IDs, and `/check`. Old POST requests receive exact decisions but are read-only.
  New clients use GET for listing and `/check` for switches, so these calls also
  work against earlier policy services exposing those endpoints. A complete
  unified directory requires the upgraded policy service.
- Existing SQLite policy databases migrate in place. Migration does not
  overwrite stored deny rules or resurrect already retired catalog rows.
- Desktop checks visible-window permissions every 10 seconds and on focus /
  opening the picker. Provider additions appear after discovery and the next
  picker refresh, without another desktop release for an existing provider.
- Direct built-in model execution retains the existing short permission lease:
  refresh every 5 seconds, expire after 10 seconds from request start. An outage
  cannot renew this lease. Gateway authorization is checked per request. A
  policy change does not cancel an already running upstream request.
- Adding a *new provider* still requires its registry, discovery configuration
  and client/runtime adapter. This is distinct from adding models to an existing
  provider. Future providers must be added to `catalog.GROUPS` and
  `catalog_sync.PROVIDERS` together.

## Verification

Tests cover real HTTP access/check agreement, read-only legacy registration,
all-provider discovery, retirement/reappearance across service restarts,
preserved hard denials, legacy Ant Ling overrides, sanitized discovery failures,
manifest loading, permission lease expiry, account changes and dynamic gateway
authorization. Desktop tests exercise policy projection and new model metadata.
