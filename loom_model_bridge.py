from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from app.ai.model_selection_store import ModelSelectionStore
from app.ai.model_store import ModelConfigStore, StoredModel
from app.ai.reasoning import ReasoningRequest
from app.ai.reasoning_catalog import resolved_reasoning
from app.ai.reasoning_store import ReasoningConfigStore


PRIMARY_SELECTION = "builtin:minimax"
CQU_SELECTION = "builtin:cqu"
MANAGED_SELECTION_PREFIX = "managed:"
MANAGED_RELAY_BASE_URL = "https://relay.smirel.com/v1"
MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_DEFAULT_MODEL = "MiniMax-M3"
CQU_DEFAULT_MODEL = "cqu-default"
_KEYRING_SERVICE = "loom-agent"
_MANAGED_RELAY_CREDENTIAL_ALIAS = "managed/relay"
_MANAGED_RELAY_KEY_ENV = (
    "LOOM_RELAY_API_KEY",
    "SMIREL_RELAY_API_KEY",
    # Backwards-compatible aliases from the first CQU prototype. They now mean
    # "Smirel Relay customer credential", not a CQU upstream credential.
    "LOOM_CQU_API_KEY",
    "CQU_API_KEY",
)
_PRIMARY_MINIMAX_KEY_ENV = ("MINIMAX_API_KEY", "LOOM_PRIMARY_API_KEY", "LOOM_API_KEY")
_PROVISIONING_FILE_ENV = "LOOM_RELAY_PROVISIONING_FILE"
_MANAGED_MODEL_DISPLAY_NAMES = {
    MINIMAX_DEFAULT_MODEL.casefold(): "MiniMax",
    CQU_DEFAULT_MODEL.casefold(): "CQU-弘深深",
}
_MANAGED_MODEL_IDS = {
    MINIMAX_DEFAULT_MODEL.casefold(): "minimax-primary",
    CQU_DEFAULT_MODEL.casefold(): "cqu-builtin",
}


def _managed_relay_base_url(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return str(env.get("LOOM_RELAY_BASE_URL") or MANAGED_RELAY_BASE_URL).strip().rstrip("/")


def _key_from_env(names: tuple[str, ...], environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return ""


def _primary_minimax_key(environ: Mapping[str, str] | None = None) -> str:
    return _key_from_env(_PRIMARY_MINIMAX_KEY_ENV, environ)


def _credential_get(alias: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(_KEYRING_SERVICE, alias)
    except Exception:
        return None


def _credential_set(alias: str, value: str) -> None:
    try:
        import keyring

        keyring.set_password(_KEYRING_SERVICE, alias, value)
    except Exception as exc:
        raise RuntimeError(f"could not save the Relay credential in the OS credential store: {exc}") from exc


def _provisioning_paths(
    home: Path,
    environ: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> list[Path]:
    env = os.environ if environ is None else environ
    paths: list[Path] = []
    explicit = str(env.get(_PROVISIONING_FILE_ENV) or "").strip()
    if explicit:
        paths.append(Path(explicit).expanduser())
    paths.extend([
        home / "relay-credential.json",
        home / "managed-relay.json",
    ])
    if repo_root is not None:
        paths.extend([
            repo_root / "loom-relay-credential.json",
            repo_root / "relay-credential.json",
        ])
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(resolved)
    return unique


def _read_provisioning_file(path: Path) -> str:
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return ""
    if not raw.startswith("{"):
        return raw
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Relay provisioning file must contain a JSON object")
    return str(
        payload.get("apiKey")
        or payload.get("api_key")
        or payload.get("relayApiKey")
        or payload.get("relay_api_key")
        or payload.get("key")
        or ""
    ).strip()


def _consume_provisioned_relay_key(
    home: Path,
    environ: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> str:
    for path in _provisioning_paths(home, environ, repo_root):
        if not path.is_file():
            continue
        api_key = _read_provisioning_file(path)
        if not api_key:
            continue
        _credential_set(_MANAGED_RELAY_CREDENTIAL_ALIAS, api_key)
        try:
            path.unlink()
        except OSError:
            pass
        return api_key
    return ""


def _normalize_url(value: str) -> str:
    return str(value or "").strip().rstrip("/")


def _is_managed_relay_endpoint(value: str, environ: Mapping[str, str] | None = None) -> bool:
    return _normalize_url(value) == _normalize_url(_managed_relay_base_url(environ))


def _looks_like_managed_relay_connection(entry: StoredModel, environ: Mapping[str, str] | None = None) -> bool:
    if not isinstance(entry, StoredModel):
        return False
    if _is_managed_relay_endpoint(entry.base_url, environ):
        return True
    return str(entry.model or "").strip().casefold() == CQU_DEFAULT_MODEL.casefold()


def _promote_saved_relay_key(
    store: ModelConfigStore,
    environ: Mapping[str, str] | None = None,
) -> str:
    for entry in store.list_models():
        if not _looks_like_managed_relay_connection(entry, environ):
            continue
        try:
            api_key = store.secret_for(entry)
        except Exception:
            continue
        api_key = str(api_key or "").strip()
        if not api_key:
            continue
        _credential_set(_MANAGED_RELAY_CREDENTIAL_ALIAS, api_key)
        return api_key
    return ""


def _managed_relay_key(
    store: ModelConfigStore,
    environ: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> str:
    provisioned = _consume_provisioned_relay_key(store.home, environ, repo_root)
    if provisioned:
        return provisioned
    secret = str(_credential_get(_MANAGED_RELAY_CREDENTIAL_ALIAS) or "").strip()
    if secret:
        return secret
    promoted = _promote_saved_relay_key(store, environ)
    if promoted:
        return promoted
    env_key = _key_from_env(_MANAGED_RELAY_KEY_ENV, environ)
    if env_key:
        try:
            _credential_set(_MANAGED_RELAY_CREDENTIAL_ALIAS, env_key)
        except RuntimeError:
            pass
        return env_key
    return ""


def _managed_models_url(environ: Mapping[str, str] | None = None) -> str:
    return f"{_managed_relay_base_url(environ)}/models"


def _fetch_managed_model_ids(
    api_key: str,
    environ: Mapping[str, str] | None = None,
    timeout: float = 3.5,
) -> list[str]:
    api_key = str(api_key or "").strip()
    if not api_key:
        return []
    request = urllib.request.Request(
        _managed_models_url(environ),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "Loom/managed-relay",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    models: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        key = model_id.casefold()
        if key in seen:
            continue
        seen.add(key)
        models.append(model_id)
    return models


def _managed_selection_for_model(model: str) -> str:
    normalized = str(model or "").strip()
    folded = normalized.casefold()
    if folded == MINIMAX_DEFAULT_MODEL.casefold():
        return PRIMARY_SELECTION
    if folded == CQU_DEFAULT_MODEL.casefold():
        return CQU_SELECTION
    return f"{MANAGED_SELECTION_PREFIX}{urllib.parse.quote(normalized, safe='')}"


def _managed_model_from_selection(selection: str) -> str | None:
    value = str(selection or "").strip()
    if value == PRIMARY_SELECTION:
        return MINIMAX_DEFAULT_MODEL
    if value == CQU_SELECTION:
        return CQU_DEFAULT_MODEL
    if value.startswith(MANAGED_SELECTION_PREFIX):
        model = urllib.parse.unquote(value[len(MANAGED_SELECTION_PREFIX) :]).strip()
        return model or None
    return None


def _managed_profile_id(model: str) -> str:
    folded = str(model or "").strip().casefold()
    if folded in _MANAGED_MODEL_IDS:
        return _MANAGED_MODEL_IDS[folded]
    digest = hashlib.sha256(folded.encode("utf-8")).hexdigest()[:12]
    return f"managed-{digest}"


def _managed_display_name(model: str) -> str:
    value = str(model or "").strip()
    return _MANAGED_MODEL_DISPLAY_NAMES.get(value.casefold(), value)


def _safe_managed(model: str, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    model = str(model or "").strip()
    if not model:
        raise ValueError("managed model id must not be empty")
    return {
        "selection": _managed_selection_for_model(model),
        "id": _managed_profile_id(model),
        "kind": "builtin",
        "name": _managed_display_name(model),
        "adapter": "openai-compatible",
        "baseUrl": _managed_relay_base_url(environ),
        "model": model,
    }


def _safe_primary() -> dict[str, Any]:
    return _safe_managed(MINIMAX_DEFAULT_MODEL)


def _safe_cqu() -> dict[str, Any]:
    return _safe_managed(CQU_DEFAULT_MODEL)


def _safe_legacy_minimax() -> dict[str, Any]:
    profile = _safe_managed(MINIMAX_DEFAULT_MODEL)
    profile["baseUrl"] = MINIMAX_BASE_URL
    return profile


def _safe_saved(entry: StoredModel) -> dict[str, Any]:
    return {
        "selection": entry.selection,
        "id": entry.model_id,
        "kind": "saved",
        "name": entry.display_name,
        "adapter": entry.adapter.value,
        "baseUrl": entry.base_url,
        "model": entry.model,
    }


def _reasoning_key(selection: str, model: str) -> str:
    selection_key = str(selection or "").strip()
    model_key = str(model or "").strip().casefold()
    if not selection_key or not model_key:
        raise ValueError("reasoning preference requires selection and model")
    return f"{selection_key}::{model_key}"


def _saved_reasoning(profile: dict[str, Any], reasoning_store: ReasoningConfigStore) -> ReasoningRequest | None:
    selection = str(profile.get("selection") or "").strip()
    model = str(profile.get("model") or "").strip()
    if not selection or not model:
        return None
    saved = reasoning_store.get(_reasoning_key(selection, model))
    if saved is not None:
        return saved
    return reasoning_store.get(selection)


def _with_reasoning(profile: dict[str, Any], reasoning_store: ReasoningConfigStore) -> dict[str, Any]:
    capability, selected = resolved_reasoning(
        model=str(profile.get("model") or ""),
        adapter=str(profile.get("adapter") or ""),
        base_url=str(profile.get("baseUrl") or ""),
        saved=_saved_reasoning(profile, reasoning_store),
    )
    payload = dict(profile)
    if capability is not None and selected is not None:
        payload["reasoning"] = {
            **capability,
            "value": selected.value,
        }
    else:
        payload["reasoning"] = None
    return payload


def _managed_profiles(store: ModelConfigStore, environ: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    api_key = _managed_relay_key(store, environ, Path(__file__).resolve().parent)
    if api_key:
        model_ids = _fetch_managed_model_ids(api_key, environ)
        if not model_ids:
            model_ids = [MINIMAX_DEFAULT_MODEL, CQU_DEFAULT_MODEL]
    elif _primary_minimax_key(environ):
        model_ids = [MINIMAX_DEFAULT_MODEL]
    else:
        model_ids = [MINIMAX_DEFAULT_MODEL]
    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for model_id in model_ids:
        folded = str(model_id or "").strip().casefold()
        if not folded or folded in seen:
            continue
        seen.add(folded)
        if not api_key and folded == MINIMAX_DEFAULT_MODEL.casefold() and _primary_minimax_key(environ):
            profiles.append(_safe_legacy_minimax())
        else:
            profiles.append(_safe_managed(model_id, environ))
    return profiles


def _base_profile_for_selection(store: ModelConfigStore, selection: str) -> dict[str, Any]:
    requested = str(selection or "").strip()
    managed_model = _managed_model_from_selection(requested)
    if managed_model:
        return _safe_managed(managed_model)
    saved = store.model_for_selection(requested)
    if saved is None:
        raise ValueError(f"unknown model selection: {requested!r}")
    return _safe_saved(saved)


def _active_selection(store: ModelConfigStore, selection_store: ModelSelectionStore) -> str:
    saved_selection = selection_store.get()
    if saved_selection:
        try:
            _base_profile_for_selection(store, saved_selection)
            return saved_selection
        except (KeyError, ValueError):
            pass
    active = store.active_model()
    return active.selection if active is not None else PRIMARY_SELECTION


def _describe_model(
    store: ModelConfigStore,
    reasoning_store: ReasoningConfigStore,
    selection: str,
    model: str,
) -> dict[str, Any]:
    requested_model = str(model or "").strip()
    if not requested_model:
        raise ValueError("model must not be empty")
    profile = _base_profile_for_selection(store, selection)
    profile["model"] = requested_model
    return _with_reasoning(profile, reasoning_store)


def _snapshot(
    store: ModelConfigStore,
    reasoning_store: ReasoningConfigStore,
    selection_store: ModelSelectionStore,
) -> dict[str, Any]:
    managed = [_with_reasoning(profile, reasoning_store) for profile in _managed_profiles(store)]
    saved = [_with_reasoning(_safe_saved(entry), reasoning_store) for entry in store.list_models()]
    profiles = [*managed, *saved]
    primary = next(
        (profile for profile in profiles if profile.get("selection") == PRIMARY_SELECTION),
        _with_reasoning(_safe_primary(), reasoning_store),
    )
    active_selection = _active_selection(store, selection_store)
    active_profile = next((profile for profile in profiles if profile.get("selection") == active_selection), None)
    if active_profile is None:
        active_profile = primary if primary in profiles else (profiles[0] if profiles else primary)
    return {
        "primary": primary,
        "profiles": profiles or [primary],
        "activeModelId": str(active_profile.get("id") or "") or None,
    }


def _resolve(
    store: ModelConfigStore,
    reasoning_store: ReasoningConfigStore,
    selection_store: ModelSelectionStore,
    selection: str | None,
) -> dict[str, Any]:
    explicit_selection = str(selection or "").strip()
    requested = explicit_selection or _active_selection(store, selection_store)
    profile = _with_reasoning(_base_profile_for_selection(store, requested), reasoning_store)

    if _managed_model_from_selection(requested):
        api_key = _managed_relay_key(store, repo_root=Path(__file__).resolve().parent)
        if api_key:
            return {**profile, "provider": "openai-compatible", "apiKey": api_key}
        if requested == PRIMARY_SELECTION:
            legacy_key = _primary_minimax_key()
            if legacy_key:
                legacy_profile = _with_reasoning(_safe_legacy_minimax(), reasoning_store)
                return {**legacy_profile, "provider": "openai-compatible", "apiKey": legacy_key}
        if not explicit_selection:
            legacy_key = _primary_minimax_key()
            if legacy_key:
                legacy_profile = _with_reasoning(_safe_legacy_minimax(), reasoning_store)
                return {**legacy_profile, "provider": "openai-compatible", "apiKey": legacy_key}
        raise RuntimeError(
            "Smirel Relay credential is not provisioned. Build or install Loom with "
            "loom-relay-credential.json, or set LOOM_RELAY_API_KEY for development."
        )

    saved = store.model_for_selection(requested)
    if saved is None:
        raise ValueError(f"unknown model selection: {requested!r}")
    return {
        **profile,
        "provider": saved.adapter.value,
        "apiKey": store.secret_for(saved),
    }


def _read_stdin_object() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("model bridge input must be a JSON object")
    return payload


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _save(store: ModelConfigStore, payload: dict[str, Any]) -> dict[str, Any]:
    entry = store.save_model(
        display_name=str(payload.get("name") or ""),
        adapter=str(payload.get("adapter") or "openai-compatible"),
        base_url=str(payload.get("baseUrl") or ""),
        model=str(payload.get("model") or ""),
        api_key=str(payload.get("apiKey") or ""),
        vision=bool(payload.get("vision", True)),
    )
    return _safe_saved(entry)


def _set_active(
    store: ModelConfigStore,
    reasoning_store: ReasoningConfigStore,
    selection_store: ModelSelectionStore,
    payload: dict[str, Any],
) -> dict[str, Any]:
    selection = str(payload.get("selection") or "").strip() or PRIMARY_SELECTION
    if _managed_model_from_selection(selection):
        store.set_active(None)
    else:
        saved = store.model_for_selection(selection)
        if saved is None:
            raise ValueError(f"unknown model selection: {selection!r}")
        store.set_active(saved.model_id)
    selection_store.set(selection)
    return _snapshot(store, reasoning_store, selection_store)


def _set_reasoning(
    store: ModelConfigStore,
    reasoning_store: ReasoningConfigStore,
    payload: dict[str, Any],
) -> dict[str, Any]:
    selection = str(payload.get("selection") or "").strip()
    model = str(payload.get("model") or "").strip()
    if not selection:
        raise ValueError("selection must not be empty")
    described = _describe_model(store, reasoning_store, selection, model)
    capability = described.get("reasoning")
    if not isinstance(capability, dict):
        raise ValueError("the selected model does not advertise reasoning controls")
    requested = ReasoningRequest.from_values(payload.get("kind"), payload.get("value"))
    if requested is None:
        raise ValueError("reasoning kind and value are required")
    if requested.kind.value != str(capability.get("kind") or ""):
        raise ValueError("reasoning kind is not supported by the selected model")
    supported = {
        str(item.get("value") or "")
        for item in capability.get("options") or []
        if isinstance(item, dict)
    }
    if requested.value not in supported:
        raise ValueError(f"reasoning value {requested.value!r} is not supported by the selected model")
    reasoning_store.set(_reasoning_key(selection, model), requested)
    return _describe_model(store, reasoning_store, selection, model)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    commands = {"list", "resolve", "describe-model", "save", "set-active", "set-reasoning"}
    if len(args) != 1 or args[0] not in commands:
        sys.stderr.write(
            "usage: loom_model_bridge.py {list|resolve|describe-model|save|set-active|set-reasoning}\n"
        )
        return 2

    command = args[0]
    try:
        store = ModelConfigStore()
        reasoning_store = ReasoningConfigStore(store.home)
        selection_store = ModelSelectionStore(store.home)
        payload = _read_stdin_object()
        if command == "list":
            result = _snapshot(store, reasoning_store, selection_store)
        elif command == "resolve":
            result = _resolve(
                store,
                reasoning_store,
                selection_store,
                str(payload.get("selection") or "") or None,
            )
        elif command == "describe-model":
            result = _describe_model(
                store,
                reasoning_store,
                str(payload.get("selection") or ""),
                str(payload.get("model") or ""),
            )
        elif command == "save":
            result = _save(store, payload)
        elif command == "set-active":
            result = _set_active(store, reasoning_store, selection_store, payload)
        else:
            result = _set_reasoning(store, reasoning_store, payload)
        _write({"ok": True, "result": result})
        return 0
    except Exception as exc:
        _write({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
