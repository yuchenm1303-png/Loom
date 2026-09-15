from __future__ import annotations

"""Public release-time defaults for first-party connectors.

A GitHub device-flow client ID is public application metadata, not a secret. A
Loom distributor can register its GitHub OAuth/GitHub App, enable Device Flow,
and commit or inject that client ID here before packaging. Environment variables
remain higher-priority so development, CI, and managed distributions can
override the baked default without changing source.

Do not put client secrets, private keys, access tokens, or refresh tokens here.
Device Flow does not require a client secret in the desktop application.
"""

import os


# Populate this with the public client ID of the GitHub application registered
# for the Loom distribution. Leaving it blank keeps the safe fallbacks (`gh`
# browser login and PAT import) available.
GITHUB_CLIENT_ID = ""
GITHUB_OAUTH_SCOPES = "repo read:org"


def install() -> None:
    client_id = str(GITHUB_CLIENT_ID or "").strip()
    scopes = str(GITHUB_OAUTH_SCOPES or "").strip()
    if client_id and not str(os.environ.get("LOOM_GITHUB_CLIENT_ID") or "").strip():
        os.environ["LOOM_GITHUB_CLIENT_ID"] = client_id
    if scopes and not str(os.environ.get("LOOM_GITHUB_OAUTH_SCOPES") or "").strip():
        os.environ["LOOM_GITHUB_OAUTH_SCOPES"] = scopes


__all__ = ["GITHUB_CLIENT_ID", "GITHUB_OAUTH_SCOPES", "install"]
