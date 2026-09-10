from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib import error as urlerror
from urllib import request as urlrequest


MANAGED_RELAY_BASE_URL = "https://relay.smirel.com/v1"
MANAGED_RELAY_KEYRING_SERVICE = "loom-agent"
MANAGED_RELAY_CREDENTIAL_ALIAS = "managed-relay/default"
MANAGED_RELAY_KEY_ENV = "LOOM_MANAGED_RELAY_KEY"
MANAGED_RELAY_PROVISION_FILENAME = "managed-relay.provision.json"


class ManagedRelayError(RuntimeError):
    pass


SecretGetter = Callable[[str, str], str | None]
SecretSetter = Callable[[str, str, str], None]
Fetcher = Callable[[urlrequest.Request, float], bytes]


def _default_home() -> Path:
    raw = str(os.environ.get("LOOM_HOME") or "").strip()
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".loom").resolve()


def _default_secret_getter(service: str, alias: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(service, alias)
    except Exception as exc:  # pragma: no cover - depends on host keyring backend
        raise ManagedRelayError(f"could not access the OS credential store: {exc}") from exc


def _default_secret_setter(service: str, alias: str, value: str) -> None:
    try:
        import keyring

        keyring.set_password(service, alias, value)
    except Exception as exc:  # pragma: no cover - depends on host keyring backend
        raise ManagedRelayError(f"could not save managed access in the OS credential store: {exc}") from exc


def _default_fetcher(req: urlrequest.Request, timeout: float) -> bytes:
    with urlrequest.urlopen(req, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS base URL
        return response.read()


@dataclass(slots=True)
class ManagedRelay:
    """Smirel-managed access used by Loom's official built-in models.

    This credential identifies one Loom customer/device at the Smirel relay. It
    is intentionally different from every upstream provider secret. The relay
    remains authoritative for model visibility, quotas and revocation.

    A customer-specific installer may place `managed-relay.provision.json` in
    LOOM_HOME before first launch. Loom imports it into the OS credential store
    and removes the plaintext file immediately after a successful import, so
    the customer never sees an API-key setup screen.
    """

    base_url: str = MANAGED_RELAY_BASE_URL
    timeout_seconds: float = 5.0
    environ: Mapping[str, str] | None = None
    provision_path: Path | None = None
    secret_getter: SecretGetter = _default_secret_getter
    secret_setter: SecretSetter = _default_secret_setter
    fetcher: Fetcher = _default_fetcher

    def _provision_file(self) -> Path:
        if self.provision_path is not None:
            return Path(self.provision_path).expanduser().resolve()
        return _default_home() / MANAGED_RELAY_PROVISION_FILENAME

    def _consume_provision_file(self) -> str:
        path = self._provision_file()
        if not path.is_file():
            return ""
        try:
            if path.stat().st_size > 64 * 1024:
                raise ManagedRelayError("managed provisioning file is unexpectedly large")
            payload = json.loads(path.read_text(encoding="utf-8"))
        except ManagedRelayError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManagedRelayError("could not read the Loom managed provisioning file") from exc
        if not isinstance(payload, dict):
            raise ManagedRelayError("managed provisioning file must contain a JSON object")
        credential = str(payload.get("credential") or payload.get("apiKey") or "").strip()
        if not credential:
            raise ManagedRelayError("managed provisioning file does not contain a credential")
        self.provision(credential)
        try:
            path.unlink()
        except OSError as exc:
            raise ManagedRelayError(
                "managed access was saved, but the one-time provisioning file could not be removed"
            ) from exc
        return credential

    def credential(self, *, required: bool = True) -> str:
        env = os.environ if self.environ is None else self.environ
        env_value = str(env.get(MANAGED_RELAY_KEY_ENV) or "").strip()
        try:
            value = str(
                self.secret_getter(MANAGED_RELAY_KEYRING_SERVICE, MANAGED_RELAY_CREDENTIAL_ALIAS)
                or ""
            ).strip()
        except ManagedRelayError:
            if env_value:
                return env_value
            raise
        if not value:
            value = self._consume_provision_file()
        if not value:
            value = env_value
        if required and not value:
            raise ManagedRelayError(
                "Loom managed access is not provisioned on this device. "
                "Install or sign in with an authorized Loom build."
            )
        return value

    def provision(self, credential: str) -> None:
        value = str(credential or "").strip()
        if not value:
            raise ValueError("managed relay credential must not be empty")
        self.secret_setter(
            MANAGED_RELAY_KEYRING_SERVICE,
            MANAGED_RELAY_CREDENTIAL_ALIAS,
            value,
        )

    def available_models(self) -> tuple[str, ...]:
        credential = self.credential(required=True)
        endpoint = self.base_url.rstrip("/") + "/models"
        req = urlrequest.Request(
            endpoint,
            method="GET",
            headers={
                "Authorization": f"Bearer {credential}",
                "Accept": "application/json",
                "User-Agent": "Loom-Managed-Relay/1",
            },
        )
        try:
            raw = self.fetcher(req, float(self.timeout_seconds))
        except urlerror.HTTPError as exc:
            if exc.code in {401, 403}:
                raise ManagedRelayError("Loom managed access is no longer authorized on this device") from exc
            raise ManagedRelayError(f"managed model catalog request failed with HTTP {exc.code}") from exc
        except (urlerror.URLError, TimeoutError, OSError) as exc:
            raise ManagedRelayError("could not reach the Loom managed model service") from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManagedRelayError("managed model service returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ManagedRelayError("managed model service returned an invalid model catalog")

        seen: set[str] = set()
        models: list[str] = []
        for item in payload["data"]:
            if not isinstance(item, dict):
                continue
            model = str(item.get("id") or "").strip()
            if not model or model in seen:
                continue
            seen.add(model)
            models.append(model)
        return tuple(models)


__all__ = [
    "MANAGED_RELAY_BASE_URL",
    "MANAGED_RELAY_CREDENTIAL_ALIAS",
    "MANAGED_RELAY_KEYRING_SERVICE",
    "MANAGED_RELAY_KEY_ENV",
    "MANAGED_RELAY_PROVISION_FILENAME",
    "ManagedRelay",
    "ManagedRelayError",
]
