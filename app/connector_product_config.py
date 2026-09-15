from __future__ import annotations

"""Release-time defaults for Loom's first-party GitHub connector.

The preferred desktop sign-in is GitHub's authorization-code flow with PKCE and
a loopback redirect on ``127.0.0.1``. GitHub still requires a client secret when
exchanging a web-flow authorization code. In a native/public client that value
cannot be treated as confidential: users can inspect the application binary.
PKCE + a random OAuth state protect the authorization-code handoff, while Loom
stores *user* access and refresh tokens only in the OS credential vault.

Distributors may bake the OAuth app's public/native client credentials into a
private release build or inject them at package/runtime. Never put user access
tokens, refresh tokens, PATs, GitHub App private keys, or other user secrets in
this module.
"""

import os


# Register a GitHub OAuth App with callback URL:
#   http://127.0.0.1/oauth/github/callback
# GitHub permits Loom to add a dynamic loopback port at runtime.
#
# Leave these blank in source unless the distribution intentionally treats its
# native-client credential pair as public application material. Environment
# overrides are always preferred for development and release automation.
GITHUB_CLIENT_ID = ""
GITHUB_CLIENT_SECRET = ""
GITHUB_OAUTH_SCOPES = "repo read:org"
GITHUB_OAUTH_CALLBACK_PATH = "/oauth/github/callback"


def _install_default(name: str, value: str) -> None:
    resolved = str(value or "").strip()
    if resolved and not str(os.environ.get(name) or "").strip():
        os.environ[name] = resolved


def install() -> None:
    _install_default("LOOM_GITHUB_CLIENT_ID", GITHUB_CLIENT_ID)
    _install_default("LOOM_GITHUB_CLIENT_SECRET", GITHUB_CLIENT_SECRET)
    _install_default("LOOM_GITHUB_OAUTH_SCOPES", GITHUB_OAUTH_SCOPES)
    _install_default("LOOM_GITHUB_CALLBACK_PATH", GITHUB_OAUTH_CALLBACK_PATH)


__all__ = [
    "GITHUB_CLIENT_ID",
    "GITHUB_CLIENT_SECRET",
    "GITHUB_OAUTH_CALLBACK_PATH",
    "GITHUB_OAUTH_SCOPES",
    "install",
]
