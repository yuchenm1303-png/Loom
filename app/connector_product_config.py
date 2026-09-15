from __future__ import annotations

"""Release-time defaults for Loom's first-party GitHub connector.

The preferred desktop sign-in is GitHub's authorization-code flow with PKCE and
a loopback redirect on ``127.0.0.1``. GitHub still requires a client secret when
exchanging a web-flow authorization code. In a native/public client that value
cannot be treated as confidential: users can inspect the application binary.
PKCE + a random OAuth state protect the authorization-code handoff, while Loom
stores *user* access and refresh tokens only in the OS credential vault.

Production packaging may generate ``connector_release_config_generated`` from
CI variables/secrets. That module is intentionally absent from source control
and removed from the working tree after PyInstaller has frozen it into the
private runtime. Environment variables remain higher priority at runtime.
"""

import os


try:
    from .connector_release_config_generated import (
        GITHUB_CLIENT_ID as _GENERATED_GITHUB_CLIENT_ID,
        GITHUB_CLIENT_SECRET as _GENERATED_GITHUB_CLIENT_SECRET,
        GITHUB_OAUTH_CALLBACK_PATH as _GENERATED_GITHUB_OAUTH_CALLBACK_PATH,
        GITHUB_OAUTH_SCOPES as _GENERATED_GITHUB_OAUTH_SCOPES,
    )
except ImportError:
    _GENERATED_GITHUB_CLIENT_ID = ""
    _GENERATED_GITHUB_CLIENT_SECRET = ""
    _GENERATED_GITHUB_OAUTH_SCOPES = ""
    _GENERATED_GITHUB_OAUTH_CALLBACK_PATH = ""


# Source defaults remain blank for application credentials. The Windows release
# build can inject them from GitHub Actions without committing them to history.
GITHUB_CLIENT_ID = str(_GENERATED_GITHUB_CLIENT_ID or "").strip()
GITHUB_CLIENT_SECRET = str(_GENERATED_GITHUB_CLIENT_SECRET or "").strip()
GITHUB_OAUTH_SCOPES = str(_GENERATED_GITHUB_OAUTH_SCOPES or "repo read:org").strip()
GITHUB_OAUTH_CALLBACK_PATH = str(
    _GENERATED_GITHUB_OAUTH_CALLBACK_PATH or "/oauth/github/callback"
).strip()


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
