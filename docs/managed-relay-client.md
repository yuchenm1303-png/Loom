# Managed Muxway Relay customer packaging

Loom built-in models are designed to be customer-installable without exposing upstream model keys.

The runtime flow is:

```text
Loom desktop → customer/device Relay credential → muxway.dev → server-side model entitlement → upstream provider
```

The customer/device credential is a TermRelay API key controlled by the Muxway deployment. It is not a CQU, MiniMax, OpenAI, Ant Ling, Claude, or other upstream provider key. Upstream keys stay only on the Relay server.

This is also the boundary between **Loom built-in models** and **BYOK/custom connections**:

- built-in models use the Loom/Muxway Relay credential and never ask the user for an upstream provider key;
- `Add connection` remains available for users who want to call their own provider account with their own Base URL/API key;
- the same provider can therefore exist both as a Loom-managed built-in group and as a user-owned custom connection without sharing credentials.

## Server setup

Create or choose a TermRelay group for the customer/package. The group's `models_list_config` controls both discovery and real inference authorization:

```json
{
  "enabled": true,
  "models": [
    "MiniMax-M3",
    "Ling-3.0-flash",
    "Ling-3.0-flash-VL",
    "cqu-default"
  ]
}
```

After the server-side entitlement change, hiding a model is not only a UI filter. If a client manually crafts a request for a model outside this list, Relay rejects it before routing to an upstream account.

### Ant Ling

Ant Ling is a Loom-managed built-in provider. The desktop never stores the Loom-owned `sk-studio-...` upstream credential and never calls `api.ant-ling.com` with that credential. Instead it:

1. authenticates to `https://muxway.dev/v1` with the customer/device Relay credential;
2. reads `/models` and enables only server-authorized `Ling-*` / `Ring-*` models;
3. sends inference to Muxway with the Relay credential;
4. relies on Muxway to keep the real Ant Ling upstream key server-side and to enforce the TermRelay group/model policy.

The Ant Ling provider stays visible in Loom even when the current account/device has no Ant Ling entitlement. In that state Loom shows the bundled Ling/Ring catalog as unavailable/locked rows. This is display-only: selecting or hand-crafting an unentitled model still fails the Relay entitlement check, so visibility never grants access.

Users who want to use their own Ant Ling account can still choose `Add connection` and supply `https://api.ant-ling.com/v1` plus their own key. That saved connection is separate from the Loom-managed Ant Ling provider.

## Build-side provisioning

Generate a one-time provisioning file from a customer Relay key. Read the key from an environment variable so it does not land in shell history:

```powershell
$env:LOOM_RELAY_API_KEY = "<customer-relay-key>"
python scripts/write_relay_provisioning.py --validate --output loom-relay-credential.json
Remove-Item Env:\LOOM_RELAY_API_KEY
```

On macOS/Linux:

```bash
export LOOM_RELAY_API_KEY='<customer-relay-key>'
python scripts/write_relay_provisioning.py --validate --output loom-relay-credential.json
unset LOOM_RELAY_API_KEY
```

Put `loom-relay-credential.json` next to `loom_model_bridge.py` in the customer package, or place it in the user's Loom home directory as `relay-credential.json`.

## First launch behavior

On first launch, Loom consumes the provisioning file, writes the credential to the OS credential store under the alias `managed/relay`, and then deletes the plaintext provisioning file on a best-effort basis.

After that, the customer does not need to enter any key. Loom calls `https://muxway.dev/v1/models` with the stored Relay credential and enables only models allowed for that customer group. The provisioning `baseUrl` is persisted alongside the managed Relay connection metadata so custom deployments keep using the endpoint they were packaged for.

## Updating or disabling access

Change the customer's TermRelay group or API key status on the server:

- remove `Ling-3.0-flash` / other `Ling-*` or `Ring-*` IDs to remove Ant Ling built-in access;
- remove `cqu-default` from the group list to close CQU for that customer;
- remove `MiniMax-M3` to close MiniMax;
- disable or expire the customer's API key to cut all built-in Relay access;
- move the key to another group to change the enabled model set and effective quota/policy.

No client update is required for these server-side model entitlement changes.

For per-user control, issue/provision a distinct Relay credential (or distinct TermRelay key/group binding) per user/customer. Sharing one Relay credential across all installations necessarily gives those installations the same server-side model entitlement.

## Files that must never be committed

These local files are intentionally gitignored:

```text
/loom-relay-credential.json
/relay-credential.json
```

They contain customer Relay credentials. Do not commit them, upload them to issue comments, or send them outside the intended package.
