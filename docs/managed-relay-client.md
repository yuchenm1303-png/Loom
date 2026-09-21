# Managed Muxway Relay customer packaging

Loom built-in models are designed to be customer-installable without exposing upstream model keys.

The runtime flow is:

```text
Loom desktop → customer/device Relay credential → muxway.dev → server-side model entitlement → upstream provider
```

The customer/device credential is a TermRelay API key controlled by the Muxway deployment. It is not a CQU, MiniMax, OpenAI, Claude, or other upstream provider key. Upstream keys stay only on the Relay server.

## Server setup

Create or choose a TermRelay group for the customer/package. The group's `models_list_config` controls both discovery and real inference authorization:

```json
{
  "enabled": true,
  "models": ["MiniMax-M3", "cqu-default"]
}
```

After the server-side entitlement change, hiding a model is not only a UI filter. If a client manually crafts a request for a model outside this list, Relay rejects it before routing to an upstream account.

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

After that, the customer does not need to enter any key. Loom calls `https://muxway.dev/v1/models` with the stored Relay credential and shows only models allowed for that customer group. The provisioning `baseUrl` is persisted alongside the managed Relay connection metadata so custom deployments keep using the endpoint they were packaged for.

## Updating or disabling access

Change the customer's TermRelay group or API key status on the server:

- remove `cqu-default` from the group list to close CQU for that customer;
- remove `MiniMax-M3` to close MiniMax;
- disable or expire the customer's API key to cut all access;
- move the key to another group to change the visible model set.

No client update is required for these server-side model entitlement changes.

## Files that must never be committed

These local files are intentionally gitignored:

```text
/loom-relay-credential.json
/relay-credential.json
```

They contain customer Relay credentials. Do not commit them, upload them to issue comments, or send them outside the intended package.
