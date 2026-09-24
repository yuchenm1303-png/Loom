from __future__ import annotations

"""Web Search configuration: provider choice, key storage, and status.

Two things are deliberately kept apart:

* the **provider choice** is a normal, non-secret preference and lives in
  Loom's ``settings.json`` under ``webSearch.provider``;
* the **provider API key** lives in the OS credential store (Windows Credential
  Manager / macOS Keychain / Secret Service) and is never written into
  settings.json, Session, WorldState, events, tool results, or a model request.

Everything the settings page reads comes back through :meth:`status`, which
reports booleans such as ``configured`` and never the key itself. The key is
only ever handed to the provider object that needs it, so a raw diagnostics or
state dump cannot echo it.
"""

import os
import time
from typing import Any, Mapping

from app.agent_runtime.web_search import (
    WebSearchProvider,
    web_search_provider_from_env,
    web_search_provider_from_values,
)

# The values the desktop settings page may store. "auto" means "use whatever
# the environment or a stored credential provides, otherwise Loom's keyless
# public provider"; "off" removes the web_search tool from the model entirely.
WEB_SEARCH_PROVIDER_CHOICES = ("auto", "duckduckgo", "tavily", "brave", "off")

WEB_SEARCH_KEYRING_SERVICE = "loom-agent/web-search"

# Environment fallbacks, checked in order. Operators who already export a key
# keep working without touching the settings page.
_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "brave": ("BRAVE_SEARCH_API_KEY", "LOOM_WEB_SEARCH_API_KEY"),
    "tavily": ("TAVILY_API_KEY", "LOOM_WEB_SEARCH_API_KEY"),
}

_KEYED_PROVIDERS = frozenset(_ENV_KEYS)


class WebSearchCredentialVault:
    """OS-keychain storage for web search provider keys.

    Mirrors ``app.connectors.CredentialVault`` but under its own keyring service
    and with one entry per provider, so switching provider cannot silently reuse
    the previous provider's key.
    """

    def __init__(self, service: str = WEB_SEARCH_KEYRING_SERVICE) -> None:
        self.service = str(service or WEB_SEARCH_KEYRING_SERVICE)

    @staticmethod
    def key_name(provider: str) -> str:
        return f"{str(provider or '').strip().casefold()}/api-key"

    def get(self, provider: str) -> str:
        name = self.key_name(provider)
        if not name.startswith(("brave/", "tavily/")):
            return ""
        try:
            import keyring

            return str(keyring.get_password(self.service, name) or "").strip()
        except Exception:
            return ""

    def set(self, provider: str, value: str) -> None:
        secret = str(value or "").strip()
        if not secret:
            raise ValueError("web search API key must not be empty")
        name = self.key_name(provider)
        try:
            import keyring

            keyring.set_password(self.service, name, secret)
        except Exception as exc:
            raise RuntimeError(
                f"OS credential store is unavailable: {type(exc).__name__}"
            ) from exc

    def delete(self, provider: str) -> None:
        try:
            import keyring
            from keyring.errors import PasswordDeleteError

            try:
                keyring.delete_password(self.service, self.key_name(provider))
            except PasswordDeleteError:
                pass
        except Exception:
            pass

    def configured(self, provider: str) -> bool:
        return bool(self.get(provider))


class WebSearchConfigurator:
    """Bind the stored web search preference to a live runtime."""

    def __init__(
        self,
        runtime: Any,
        settings_store: Any = None,
        *,
        vault: WebSearchCredentialVault | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.runtime = runtime
        self.settings_store = settings_store
        self.vault = vault if vault is not None else WebSearchCredentialVault()
        self._environ = environ

    def environ(self) -> Mapping[str, str]:
        return os.environ if self._environ is None else self._environ

    def preferences(self, settings: dict[str, Any] | None = None) -> dict[str, str]:
        snapshot = settings
        if snapshot is None:
            store = self.settings_store
            snapshot = store.snapshot() if store is not None else {}
        raw = snapshot.get("webSearch") if isinstance(snapshot, dict) else None
        section = dict(raw) if isinstance(raw, dict) else {}
        choice = str(section.get("provider") or "auto").strip().casefold()
        if choice not in WEB_SEARCH_PROVIDER_CHOICES:
            choice = "auto"
        return {"provider": choice}

    def _resolve(
        self,
        choice: str,
    ) -> tuple[WebSearchProvider | None, str, str]:
        """Return (provider, key_source, error). Never returns secret material."""

        env = self.environ()
        try:
            if choice == "off":
                return None, "none", ""
            if choice == "duckduckgo":
                return web_search_provider_from_values("duckduckgo"), "none", ""
            if choice in _KEYED_PROVIDERS:
                stored = self.vault.get(choice)
                if stored:
                    return web_search_provider_from_values(choice, stored), "keyring", ""
                for name in _ENV_KEYS[choice]:
                    value = str(env.get(name) or "").strip()
                    if value:
                        return web_search_provider_from_values(choice, value), "environment", ""
                return None, "none", f"{choice} Search requires an API key"
            # auto: respect the environment, then Loom's keyless public default.
            provider = web_search_provider_from_env(env)
            if provider is None:
                return None, "none", ""
            source = "environment" if provider.provider_name in _KEYED_PROVIDERS else "none"
            return provider, source, ""
        except ValueError as exc:
            # A contradictory configuration (for example a generic key with no
            # provider) is reported, not raised: the desktop must still start.
            return None, "none", str(exc)

    def apply(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        choice = self.preferences(settings)["provider"]
        provider, source, error = self._resolve(choice)
        self.runtime.web_search_install_provider(
            provider,
            choice=choice,
            key_source=source,
            error=error,
        )
        return self.status(settings=settings)

    def _key_available(self, choice: str) -> bool:
        if choice not in _KEYED_PROVIDERS:
            return False
        if self.vault.get(choice):
            return True
        env = self.environ()
        return any(str(env.get(name) or "").strip() for name in _ENV_KEYS[choice])

    def status(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        """Non-secret status for the settings page.

        Contains no API key, no key prefix, and no key length.
        """

        choice = self.preferences(settings)["provider"]
        payload: dict[str, Any] = dict(self.runtime.web_search_status())
        payload["available"] = True
        payload["choice"] = choice
        payload["keyRequired"] = choice in _KEYED_PROVIDERS
        payload["keyConfigured"] = self._key_available(choice)
        if payload.get("state") == "not_configured" and choice in _KEYED_PROVIDERS:
            payload.setdefault("reason", f"{choice} Search requires an API key")
        return payload

    def test(self, query: str = "OpenAI") -> dict[str, Any]:
        """Run one fixed, bounded search and report only non-secret facts."""

        provider = getattr(self.runtime, "web_search_provider", None)
        if provider is None:
            return {
                "ok": False,
                "provider": "disabled",
                "latencyMs": 0,
                "resultCount": 0,
                "state": "not_configured",
                "error": "web search provider is not configured",
            }
        text = str(query or "").strip() or "OpenAI"
        started = time.monotonic()
        try:
            response = provider.search(text, count=5)
        except Exception as exc:
            latency = int((time.monotonic() - started) * 1000)
            return {
                "ok": False,
                "provider": provider.provider_name,
                "latencyMs": latency,
                "resultCount": 0,
                "state": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
        latency = int((time.monotonic() - started) * 1000)
        return {
            "ok": True,
            "provider": response.provider,
            "latencyMs": latency,
            "resultCount": len(response.results),
            "state": "ready",
            "error": "",
        }

    def configure(
        self,
        *,
        provider: str | None = None,
        api_key: str = "",
        clear_key: bool = False,
    ) -> dict[str, Any]:
        """Persist a provider choice and/or a new key, then apply it live."""

        current = self.preferences()["provider"]
        target = str(provider if provider is not None else current).strip().casefold()
        if not target:
            target = current
        if target not in WEB_SEARCH_PROVIDER_CHOICES:
            raise ValueError(f"unsupported web search provider: {target}")

        secret = str(api_key or "").strip()
        if secret and target not in _KEYED_PROVIDERS:
            raise ValueError("an API key can only be stored for the Tavily or Brave provider")
        if secret:
            self.vault.set(target, secret)
        if clear_key:
            for name in sorted(_KEYED_PROVIDERS):
                self.vault.delete(name)

        if self.settings_store is not None and target != current:
            self.settings_store.set_value("webSearch.provider", target)
        self.apply()
        settings = None
        if self.settings_store is not None:
            settings = self.settings_store.snapshot()
        return {"status": self.status(settings=settings), "settings": settings}


__all__ = [
    "WEB_SEARCH_KEYRING_SERVICE",
    "WEB_SEARCH_PROVIDER_CHOICES",
    "WebSearchConfigurator",
    "WebSearchCredentialVault",
]
