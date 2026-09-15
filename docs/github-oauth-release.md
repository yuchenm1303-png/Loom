# GitHub OAuth release provisioning

Loom's normal desktop GitHub sign-in is a one-click browser OAuth flow with PKCE and a loopback callback. The application code is complete without repository-local credentials, but a production build must be provisioned once with an OAuth App identity issued by GitHub.

## 1. Register the OAuth App

Create a GitHub OAuth App for Loom and register this callback URL:

```text
http://127.0.0.1/oauth/github/callback
```

Loom binds an ephemeral port at runtime, for example `http://127.0.0.1:49321/oauth/github/callback`. GitHub permits the loopback port to vary for native apps.

Recommended scopes:

```text
repo read:org
```

## 2. Configure the repository Actions values

In the Loom repository, configure these Actions values:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `LOOM_GITHUB_CLIENT_ID` | OAuth App client ID |
| Secret | `LOOM_GITHUB_CLIENT_SECRET` | OAuth App client secret |
| Variable (optional) | `LOOM_GITHUB_OAUTH_SCOPES` | `repo read:org` |
| Variable (optional) | `LOOM_GITHUB_CALLBACK_PATH` | `/oauth/github/callback` |

The Windows packaging workflow exposes the production credential pair only to trusted `push` and `workflow_dispatch` builds. Pull-request builds receive neither member of the pair.

## 3. Packaging behavior

`desktop-react/scripts/build-windows-runtime.mjs` reads the release values and, only when both client ID and client secret are present, creates this temporary source file:

```text
app/connector_release_config_generated.py
```

PyInstaller freezes that module into Loom's private runtime. The build script deletes the temporary source file on process exit, and `.gitignore` independently blocks it from being committed.

If one member of the credential pair is present without the other, packaging fails instead of silently shipping a partially configured login flow. If neither is present, packaging remains valid but Web OAuth is not provisioned and Loom keeps Device Flow / GitHub CLI / PAT recovery paths available.

The Windows frozen-runtime smoke removes the build machine's `LOOM_GITHUB_*` environment variables before checking `WebOAuthConnectorManager.webOAuthAvailable`. This verifies that a configured release really contains the generated frozen configuration rather than accidentally passing because it inherited CI environment variables.

## Security model

GitHub's Web Application Flow requires `client_secret` during code exchange. Loom is a native/public desktop client, so an application credential embedded in an EXE cannot be considered confidential. The client secret is therefore application identification material, not Loom's security boundary.

The authorization boundary is enforced by:

- PKCE (`S256`) for every authorization attempt;
- a random per-attempt OAuth `state`, rejected at the loopback HTTP handler before a success page is shown;
- an exact `127.0.0.1` loopback callback on a random port;
- a ten-minute callback listener lifetime with automatic shutdown;
- exact redirect-URI reuse at token exchange;
- GitHub `/user` validation before any access token becomes executable authority;
- OS keychain storage for user access and refresh tokens;
- immutable per-Step connector authority inside the Agent Runtime.

User access tokens, refresh tokens and PATs are never generated into the release module and must never be stored in Actions variables, source files, installer resources, Loom JSON settings, or logs.
