# Authorization lifecycle audit (2026-10-17)

Scope: Electron identity and credential delivery, Host automation gates and model
snapshots, account session rotation/revocation, model policy and gateway checks.
This is a source/test audit, not a production availability or penetration-test certification.

## Authorities and boundaries

| Layer | Authority | Contract |
| --- | --- | --- |
| Identity | Account service session + desktop identity revision | Login/logout/account switch invalidate prior identity work. |
| Credentials | Account access token / separately scoped model token | Neither token is a permission grant; server checks remain authoritative. |
| Automation | One Host gate for browserUse and computerUse | Stable discovery; execution checks account capability and local execution policy. |
| Models | Model policy service and trusted model gateway | Managed models use central policy; personal connections do not inherit central grants. |
| Local execution | Tool permissions, sandbox and backend target checks | Account entitlement does not bypass local execution controls. |

## Findings and repairs

- Account refresh rotates the access token immediately. Previously the desktop
  waited for model-token issuance before delivering the new automation token.
  Two serialized, independent delivery lanes now replace that coupled path.
- Account/model token changes notify delivery immediately; periodic sync remains
  a fallback. Each lane fences results by desktop identity revision.
- Identity transitions suspend delivery until the current login/logout completes;
  older sign-in responses cannot overwrite a newer login or sign-out.
- RPC credential updates are partial: an omitted lane is preserved, not cleared.
  Explicit account logout clears both lanes and the model permission snapshot.
- Automation 401 failures request one recovery per credential generation. Model
  snapshot 401 failures request one recovery per credential revision. Recovery
  refreshes credentials; it never grants access or replays failed UI actions.
  Startup recovery notifications are retained until the desktop subscribes.
- Automation checks capture token and generation atomically. Both automation
  capabilities use one authorization result per execution check, avoiding a
  denial classified using a second, potentially different-account response.
  A new identity has its own single-flight lock and does not wait for an old
  identity's slow network request; status cannot mix old grants with new metadata.
- Model snapshot refreshes are fenced by credential revision, including A/B/A
  transitions. Execution checks cannot install a retired credential. Invalid
  model grants are rejected rather than coercing malformed values into grants.

## Failure semantics and leases

Explicit denial, missing credentials, 401 and transport errors remain distinct.
Automation errors fail closed; no stale authorization is extended after network
failure. Existing model leases are not extended on failed refresh. Socket timeout
is not an end-to-end DNS deadline; transport diagnostics include actual duration.
An explicit model-policy 401/403 immediately invalidates a warm local lease.

Automation grants have a maximum 30-second cache window; Host model snapshots
use 10-second leases. Admin capability changes are therefore not instantaneous
at every client. Account logout invalidates local credentials immediately; server
session revocation invalidates access/model tokens at their authoritative checks.
Already-started operations cannot be undone by a later permission change.

CLI/test modes intentionally opt out of account gating unless configured;
packaged desktop explicitly enables it. This is not an OS-level security boundary
against a user modifying their own installation.

## Evidence and remaining limits

Tests cover channel independence, forced refresh, transient failures, explicit
logout, account switching/ABA, late model responses, partial RPC updates and shared
automation denial. Existing account/model server suites cover token rotation,
revocation and policy enforcement; these are local test instances only.

No production account grants, service credentials, proxy settings or user tabs
were modified. Production INVALID_TOKEN/URLError episodes still require live
diagnostics to establish their individual causes. Local tests do not establish
production uptime or prove every race has been eliminated.
