# Loom Model Gateway

Loom built-in models are authenticated with the user's Loom account and routed through a Loom-owned gateway. Upstream provider credentials never ship in the desktop or web client.

```text
Loom Desktop / Loom Host
        │ scoped Loom model credential
        ▼
https://account.smirel.com/model/v1
        │
        ├─ validate account with account.smirel.com
        ├─ enforce per-user model entitlement
        └─ use server-side provider credential
                │
                └─ Ant Ling / future Loom-managed providers
```

## Built-in versus BYOK

Built-in provider rows in the Loom model picker are Loom-managed. The user does not enter a provider key. `Add connection` remains separate and stores a user's own provider key in their OS credential store.

For Ant Ling, the built-in group uses `https://account.smirel.com/model/v1`. A user-owned Ant Ling connection may still use `https://api.ant-ling.com/v1` directly.

## Authentication boundary

Electron main owns the Loom account session. When a built-in model declares `authMode=loom-account`, Electron main mints a scoped Loom model credential tied to the current login session immediately before sending the model spec to the local Agent Runtime. The React renderer never receives the token.

The gateway forwards neither that token nor any desktop credential to the upstream provider. It replaces it with the Loom-owned upstream credential stored only in the gateway environment.

## Entitlements

The account service stores optional per-user overrides in `model_entitlements`. Without an override, `LOOM_DEFAULT_BUILTIN_MODELS` (or Loom's bundled defaults) applies.

Admins can change a user's model access from `admin.smirel.com`. The gateway checks entitlement on every inference request, so hiding or disabling a model is enforced server-side rather than only in the UI.

## Deployment secrets

The gateway reads Ant Ling's upstream secret from `LOOM_ANT_LING_API_KEY`. A populated `.env.models` must never be committed. The desktop must never receive this value.
