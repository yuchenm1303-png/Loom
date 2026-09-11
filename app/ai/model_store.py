from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .credentials import CredentialRef
from .profiles import ModelContextLimits
from .provider_catalog import ProviderAdapter, ProviderConnection, provider_descriptor


_CONFIG_VERSION = 1
_KEYRING_SERVICE = "loom-agent"
_SELECTION_PREFIX = "profile:"
_MODEL_ID_RE = re.compile(r"^m[a-z0-9]{12}$")

SecretGetter = Callable[[str], str | None]
SecretSetter = Callable[[str, str], None]
SecretDeleter = Callable[[str], None]


class ModelStoreError(RuntimeError):
    pass


def _default_home() -> Path:
    raw = str(os.environ.get("LOOM_HOME") or "").strip()
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".loom").resolve()


def model_selection(model_id: str) -> str:
    model_id = str(model_id or "").strip().casefold()
    if not _MODEL_ID_RE.fullmatch(model_id):
        raise ValueError(f"invalid stored model id: {model_id!r}")
    return f"{_SELECTION_PREFIX}{model_id}"


def model_id_from_selection(value: str | None) -> str | None:
    raw = str(value or "").strip().casefold()
    if not raw.startswith(_SELECTION_PREFIX):
        return None
    model_id = raw[len(_SELECTION_PREFIX) :]
    return model_id if _MODEL_ID_RE.fullmatch(model_id) else None


def _optional_int(payload: dict[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    return int(value)


def _context_limits_from_dict(payload: object) -> ModelContextLimits:
    if not isinstance(payload, dict):
        return ModelContextLimits()
    return ModelContextLimits(
        context_window_tokens=_optional_int(payload, "context_window_tokens"),
        effective_context_percent=int(payload.get("effective_context_percent") or 95),
        auto_compact_token_limit=_optional_int(payload, "auto_compact_token_limit"),
        output_reserve_tokens=_optional_int(payload, "output_reserve_tokens"),
        tool_output_token_limit=_optional_int(payload, "tool_output_token_limit"),
    )


@dataclass(frozen=True, slots=True)
class StoredModel:
    """Secret-free metadata for one user-configured Agent model endpoint."""

    model_id: str
    display_name: str
    adapter: ProviderAdapter
    base_url: str
    model: str
    credential_alias: str
    # Declared, not detected. An OpenAI-compatible base URL says nothing about
    # whether the model behind it accepts image parts, and guessing from the
    # model name is wrong for exactly the self-hosted endpoints this field
    # exists to serve. Default on, because the common case is a modern model
    # and the cost of being wrong is one clear provider error.
    vision: bool = True
    # Context limits are endpoint/model metadata, not provider-name guesses.
    # Old model records omit this field and therefore retain conservative runtime
    # defaults until the user or managed catalog supplies authoritative values.
    context_limits: ModelContextLimits = field(default_factory=ModelContextLimits)

    def __post_init__(self) -> None:
        model_id = str(self.model_id or "").strip().casefold()
        if not _MODEL_ID_RE.fullmatch(model_id):
            raise ValueError(f"invalid stored model id: {self.model_id!r}")
        display_name = " ".join(str(self.display_name or "").split())
        model = str(self.model or "").strip()
        credential_alias = str(self.credential_alias or "").strip()
        if not display_name:
            raise ValueError("model connection name must not be empty")
        if not model:
            raise ValueError("model name must not be empty")
        adapter = ProviderAdapter(self.adapter)
        descriptor = provider_descriptor(adapter)
        if not descriptor.executable:
            raise ValueError(f"provider adapter is not executable: {adapter.value}")
        if adapter not in {ProviderAdapter.OPENAI, ProviderAdapter.OPENAI_COMPATIBLE}:
            raise ValueError(f"unsupported saved model adapter: {adapter.value}")
        if not isinstance(self.context_limits, ModelContextLimits):
            raise TypeError("context_limits must be ModelContextLimits")

        # Reuse ProviderConnection validation so saved endpoints follow exactly
        # the same URL and credential-reference rules as the runtime.
        connection = ProviderConnection(
            provider_id=f"model-{model_id}",
            adapter=adapter,
            credential_ref=CredentialRef.os_keychain(credential_alias),
            base_url=str(self.base_url or ""),
            display_name=display_name,
        )
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "adapter", adapter)
        object.__setattr__(self, "base_url", connection.base_url)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "credential_alias", credential_alias)
        object.__setattr__(self, "vision", bool(self.vision))

    @property
    def selection(self) -> str:
        return model_selection(self.model_id)

    def provider_connection(self) -> ProviderConnection:
        return ProviderConnection(
            provider_id=f"model-{self.model_id}",
            adapter=self.adapter,
            credential_ref=CredentialRef.os_keychain(self.credential_alias),
            base_url=self.base_url,
            display_name=self.display_name,
        )

    def as_safe_dict(self) -> dict[str, object]:
        return {
            "id": self.model_id,
            "name": self.display_name,
            "adapter": self.adapter.value,
            "base_url": self.base_url,
            "model": self.model,
            "credential_alias": self.credential_alias,
            "vision": self.vision,
            "context_limits": self.context_limits.as_safe_dict(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "StoredModel":
        # Entries written before attachments/context metadata existed omit these
        # fields. Additive parsing keeps v1 files forward-compatible.
        vision = payload.get("vision")
        return cls(
            model_id=str(payload.get("id") or ""),
            display_name=str(payload.get("name") or ""),
            adapter=ProviderAdapter(str(payload.get("adapter") or "")),
            base_url=str(payload.get("base_url") or ""),
            model=str(payload.get("model") or ""),
            credential_alias=str(payload.get("credential_alias") or ""),
            vision=True if vision is None else bool(vision),
            context_limits=_context_limits_from_dict(payload.get("context_limits")),
        )


class ModelConfigStore:
    """Persistent model metadata with API keys kept in the OS credential store."""

    def __init__(
        self,
        home: str | Path | None = None,
        *,
        secret_getter: SecretGetter | None = None,
        secret_setter: SecretSetter | None = None,
        secret_deleter: SecretDeleter | None = None,
    ) -> None:
        self.home = Path(home).expanduser().resolve() if home is not None else _default_home()
        self.path = self.home / "models.json"
        self._secret_getter = secret_getter
        self._secret_setter = secret_setter
        self._secret_deleter = secret_deleter

    def _read_payload(self) -> dict[str, object]:
        if not self.path.is_file():
            return {"version": _CONFIG_VERSION, "active_model_id": None, "models": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelStoreError(f"could not read model configuration: {exc}") from exc
        if not isinstance(payload, dict):
            raise ModelStoreError("model configuration root must be a JSON object")
        if int(payload.get("version") or 0) != _CONFIG_VERSION:
            raise ModelStoreError("unsupported model configuration version")
        models = payload.get("models")
        if not isinstance(models, list):
            raise ModelStoreError("model configuration models must be a list")
        return payload

    def _write_payload(self, payload: dict[str, object]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise ModelStoreError(f"could not save model configuration: {exc}") from exc

    def list_models(self) -> tuple[StoredModel, ...]:
        payload = self._read_payload()
        parsed: list[StoredModel] = []
        for raw in payload.get("models") or []:
            if not isinstance(raw, dict):
                raise ModelStoreError("stored model entry must be a JSON object")
            try:
                parsed.append(StoredModel.from_dict(raw))
            except (TypeError, ValueError) as exc:
                raise ModelStoreError(f"invalid stored model entry: {exc}") from exc
        return tuple(parsed)

    @property
    def active_model_id(self) -> str | None:
        raw = str(self._read_payload().get("active_model_id") or "").strip().casefold()
        return raw if _MODEL_ID_RE.fullmatch(raw) else None

    def get(self, model_id: str) -> StoredModel:
        key = str(model_id or "").strip().casefold()
        for entry in self.list_models():
            if entry.model_id == key:
                return entry
        raise KeyError(f"unknown stored model: {key or model_id!r}")

    def active_model(self) -> StoredModel | None:
        model_id = self.active_model_id
        if model_id is None:
            return None
        try:
            return self.get(model_id)
        except KeyError:
            return None

    def model_for_selection(self, selection: str | None) -> StoredModel | None:
        model_id = model_id_from_selection(selection)
        if model_id is None:
            return None
        return self.get(model_id)

    def save_model(
        self,
        *,
        display_name: str,
        adapter: ProviderAdapter | str,
        base_url: str,
        model: str,
        api_key: str,
        vision: bool = True,
        context_limits: ModelContextLimits | None = None,
    ) -> StoredModel:
        display_name = " ".join(str(display_name or "").split())
        api_key = str(api_key or "").strip()
        if not display_name:
            raise ValueError("connection name must not be empty")
        if not api_key:
            raise ValueError("API key must not be empty")
        if any(entry.display_name.casefold() == display_name.casefold() for entry in self.list_models()):
            raise ValueError(f"a model connection named {display_name!r} already exists")

        model_id = "m" + uuid.uuid4().hex[:12]
        entry = StoredModel(
            model_id=model_id,
            display_name=display_name,
            adapter=ProviderAdapter(adapter),
            base_url=base_url,
            model=model,
            credential_alias=f"model/{model_id}",
            vision=bool(vision),
            context_limits=context_limits or ModelContextLimits(),
        )
        self._set_secret(entry.credential_alias, api_key)
        payload = self._read_payload()
        models = list(payload.get("models") or [])
        models.append(entry.as_safe_dict())
        payload["version"] = _CONFIG_VERSION
        payload["models"] = models
        self._write_payload(payload)
        return entry

    def update_model(
        self,
        model_id: str,
        *,
        display_name: str,
        adapter: ProviderAdapter | str,
        base_url: str,
        model: str,
        api_key: str | None = None,
        vision: bool = True,
        context_limits: ModelContextLimits | None = None,
    ) -> StoredModel:
        """Update one saved connection without changing its stable selection id.

        A blank/omitted API key preserves the existing keychain secret. Supplying
        a non-empty key replaces it only after all metadata validation succeeds.
        """
        current = self.get(model_id)
        display_name = " ".join(str(display_name or "").split())
        if not display_name:
            raise ValueError("connection name must not be empty")
        if any(
            entry.model_id != current.model_id and entry.display_name.casefold() == display_name.casefold()
            for entry in self.list_models()
        ):
            raise ValueError(f"a model connection named {display_name!r} already exists")

        updated = StoredModel(
            model_id=current.model_id,
            display_name=display_name,
            adapter=ProviderAdapter(adapter),
            base_url=base_url,
            model=model,
            credential_alias=current.credential_alias,
            vision=bool(vision),
            context_limits=context_limits if context_limits is not None else current.context_limits,
        )
        next_key = str(api_key or "").strip()
        if next_key:
            self._set_secret(updated.credential_alias, next_key)

        payload = self._read_payload()
        models: list[object] = []
        replaced = False
        for raw in payload.get("models") or []:
            if isinstance(raw, dict) and str(raw.get("id") or "").strip().casefold() == current.model_id:
                models.append(updated.as_safe_dict())
                replaced = True
            else:
                models.append(raw)
        if not replaced:
            raise KeyError(f"unknown stored model: {current.model_id!r}")
        payload["version"] = _CONFIG_VERSION
        payload["models"] = models
        self._write_payload(payload)
        return updated

    def delete_model(self, model_id: str) -> StoredModel:
        entry = self.get(model_id)
        payload = self._read_payload()
        remaining: list[object] = []
        removed = False
        for raw in payload.get("models") or []:
            if isinstance(raw, dict) and str(raw.get("id") or "").strip().casefold() == entry.model_id:
                removed = True
                continue
            remaining.append(raw)
        if not removed:
            raise KeyError(f"unknown stored model: {entry.model_id!r}")
        payload["version"] = _CONFIG_VERSION
        payload["models"] = remaining
        if str(payload.get("active_model_id") or "").strip().casefold() == entry.model_id:
            payload["active_model_id"] = None
        self._write_payload(payload)
        self._delete_secret(entry.credential_alias)
        return entry

    def set_active(self, model_id: str | None) -> None:
        payload = self._read_payload()
        if model_id is None:
            payload["active_model_id"] = None
        else:
            entry = self.get(model_id)
            payload["active_model_id"] = entry.model_id
        self._write_payload(payload)

    def secret_for(self, entry: StoredModel) -> str:
        if not isinstance(entry, StoredModel):
            raise TypeError("entry must be StoredModel")
        value = self._get_secret(entry.credential_alias)
        secret = str(value or "").strip()
        if not secret:
            raise ModelStoreError(
                f"API key is missing from the OS credential store for {entry.display_name!r}"
            )
        return secret

    def _get_secret(self, alias: str) -> str | None:
        if self._secret_getter is not None:
            return self._secret_getter(alias)
        try:
            import keyring

            return keyring.get_password(_KEYRING_SERVICE, alias)
        except Exception as exc:
            raise ModelStoreError(f"could not access the OS credential store: {exc}") from exc

    def _set_secret(self, alias: str, value: str) -> None:
        if self._secret_setter is not None:
            self._secret_setter(alias, value)
            return
        try:
            import keyring

            keyring.set_password(_KEYRING_SERVICE, alias, value)
        except Exception as exc:
            raise ModelStoreError(f"could not save the API key in the OS credential store: {exc}") from exc

    def _delete_secret(self, alias: str) -> None:
        if self._secret_deleter is not None:
            self._secret_deleter(alias)
            return
        try:
            import keyring

            keyring.delete_password(_KEYRING_SERVICE, alias)
        except Exception:
            # Metadata deletion is the source of truth. A stale OS credential is
            # unreachable without the deleted model metadata, so deletion should
            # not fail just because the platform keychain reports "not found".
            return


__all__ = [
    "ModelConfigStore",
    "ModelStoreError",
    "StoredModel",
    "model_id_from_selection",
    "model_selection",
]
