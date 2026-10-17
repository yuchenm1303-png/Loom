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
3. A model missing from one complete provider listing enters `pending-removal`
   and stays available. Retirement requires at least two successful observations
   separated by 300 seconds. Failed discovery cannot confirm retirement; a
   reappearing model clears the pending confirmation. A trusted operator manifest
   can retire a model immediately. Their catalog
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
   administrator denials retain their policy source. Policy outages preserve the
   last same-account view and display synchronization status; they do not create
   new administrator denials. Actual inference still requires server authorization.

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
  New clients use GET for listing. Choosing a model checks fresh cached denials;
  it does not wait on a policy network round trip. Actual execution continues
  through server authorization. A complete
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

## Provider isolation and refresh visibility

The authenticated envelope (`enabled`, account identity, transport status) is
validated as a whole. Schema-2 directory rows and decisions then reconcile per
provider in `modelCatalogSync.ts`. Malformed rows, duplicate IDs, missing model
decisions or a missing previously seen group preserve only that group's previous
rows and decisions. Healthy groups advance immediately. Groups unsupported by
this Host are reported and omitted from its launchable directory; they never
receive an invented transport. Complete malformed/empty envelopes retain the
previous account snapshot and show the global synchronization warning.

Retained provider decisions are presentation data. Single-model authorization
checks bypass the display cache for an invalid group. Account-wide denial still
applies to every group, including retained rows. `/check` validates its exact
target decision rather than interpreting its accompanying directory as a full
catalog refresh. Every actual built-in inference remains gateway-authorized.

The picker shows provider discovery errors and its last successful discovery
timestamp, plus the last successful permission check when that check is stale.
Its Refresh action uses the existing forced Desktop refresh; it does not trigger
an unauthenticated administrator discovery operation. Discovery runs every 300
seconds by default; visible clients poll every 10 seconds and policy snapshots
are fresh for 20 seconds. Those are expected propagation bounds, not an instant
push guarantee. Upstream failures keep the last directory with a stale status.

Concurrent Host list requests share their work. Model metadata reconciliation
uses generations so a late response cannot overwrite a newer directory.
Capability-cache write failures show a warning and do not suppress directory
updates. New model descriptions are batched per provider, with independent
fallback for Hosts lacking a batch operation. Invalid manifest groups and
unrecognized future manifest groups do not stop other providers discovering.

`modelCatalogPipeline.test.mjs` starts a loopback upstream and the real Python
policy HTTP service over SQLite. It adds Beam after initial discovery, corrupts
Console's response, then exercises the real account client, catalog reconciliation,
model manager and access projection. Beam becomes selectable while Console's
previous rows survive. The runtime metadata bridge is isolated in this test;
provider inference availability is a separate contract. CI explicitly installs
Python for this Electron test; local runs may set `LOOM_TEST_PYTHON`.

## Verification

Tests cover real HTTP access/check agreement, read-only legacy registration,
all-provider discovery, retirement/reappearance across service restarts,
preserved hard denials, legacy Ant Ling overrides, sanitized discovery failures,
manifest loading, permission lease expiry, account changes and dynamic gateway
authorization. Desktop tests exercise policy projection and new model metadata.

## Connection and concurrency invariants

- A selection ID, rather than a model name, identifies a connection. Built-in
  selections require the canonical account gateway and account authentication;
  `profile:` and legacy `saved:` selections require personal authentication.
  Desktop and Python validate this before materializing credentials. Sharing a
  model name never turns a personal connection into an account connection.
- Catalog availability, access decisions and policy synchronization are distinct
  fields. Every list, switch and reasoning response uses the same pure access
  projection. Applying it repeatedly cannot overwrite catalog state. Server
  catalog state survives a local bootstrap registry refresh.
- Policy reads coalesce per authenticated identity. Stale responses cannot cross
  logout/login, including login to the same user. Failed reads have a short
  cooldown; presentation waits at most 150 ms for that policy read and otherwise
  uses its current cache. This is not an inference authorization lease.
- Thread switches serialize per thread. Default preference writes serialize
  separately, and only the latest requested preference may become current.
  Opening an old thread does not overwrite the default for new conversations.
  Renderer refresh generations discard responses superseded by a newer refresh
  or acknowledged switch. Existing threads use current connection metadata for
  their exact selection, never another connection with the same model name.
- An old built-in binding missing before a confirmed catalog arrives is shown
  as syncing. Confirmed retirement and a deleted personal connection remain
  unavailable. These states do not silently redirect a conversation.

Regression checks exercise policy outages and recovery, account identity races,
concurrent preference persistence, old-thread projection, retirement confirmation
and reappearance, personal route preservation, and isolated container imports.
External provider outages and genuine policy denials remain possible; they must
be reported without changing connection identity or inventing new permissions.
