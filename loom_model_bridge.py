from __future__ import annotations

import json
import sys
from typing import Any

from app.ai.managed_relay import MANAGED_RELAY_BASE_URL, ManagedRelay, ManagedRelayError
from app.ai.model_selection_store import ModelSelectionStore
from app.ai.model_store import ModelConfigStore, StoredModel
from app.ai.reasoning import ReasoningRequest
from app.ai.reasoning_catalog import resolved_reasoning
from app.ai.reasoning_store import ReasoningConfigStore


PRIMARY_SELECTION = "builtin:minimax"
CQU_SELECTION = "builtin:cqu"
MINIMAX_BASE_URL = MANAGED_RELAY_BASE_URL
MINIMAX_DEFAULT_MODEL = "MiniMax-M3"
CQU_BASE_URL = MANAGED_RELAY_BASE_URL
CQU_DEFAULT_MODEL = "cqu-default"
_BUILTIN_SELECTIONS = {PRIMARY_SELECTION, CQU_SELECTION}


def _safe_saved(entry: StoredModel) -> dict[str, Any]:
    return {
        "selection": entry.selection,
        "id": entry.model_id,
        "kind": "saved",
        "name": entry.display_name,
        "adapter": entry.adapter.value,
        "baseUrl": entry.base_url,
        "model": entry.model,
        "managed": False,
        "available": True,
        "availabilityReason": "",
    }


def _safe_primary() -> dict[str, Any]:
    return {
        "selection": PRIMARY_SELECTION,
        "id": "minimax-primary",
        "kind": "builtin",
        "name": "MiniMax",
        "adapter": "openai-compatible",
        "baseUrl": MINIMAX_BASE_URL,
        "model": MINIMAX_DEFAULT_MODEL,
        "managed": True,
    }


def _safe_cqu() -> dict[str, Any]:
    return {
        "selection": CQU_SELECTION,
        "id": "cqu-builtin",
        "kind": "builtin",
        "name": "CQU-弘深深",
        "adapter": "openai-compatible",
        "baseUrl": CQU_BASE_URL,
        "model": CQU_DEFAULT_MODEL,
        "managed": True,
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


def _base_profile_for_selection(store: ModelConfigStore, selection: str) -> dict[str, Any]:
    requested = str(selection or "").strip()
    if requested == PRIMARY_SELECTION:
        return _safe_primary()
    if requested == CQU_SELECTION:
        return _safe_cqu()
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


def _managed_catalog(relay: ManagedRelay) -> tuple[set[str], str]:
    try:
        if not relay.credential(required=False):
            return set(), "Managed access is not provisioned on this device."
        return set(relay.available_models()), ""
    except ManagedRelayError as exc:
        return set(), str(exc)


def _with_managed_availability(
    profile: dict[str, Any],
    allowed_models: set[str],
    catalog_error: str,
) -> dict[str, Any]:
    payload = dict(profile)
    model = str(payload.get("model") or "").strip()
    available = model in allowed_models
    payload["available"] = available
    payload["availabilityReason"] = "" if available else (
        catalog_error or f"{model} is not enabled for this Loom account."
    )
    return payload


def _snapshot(
    store: ModelConfigStore,
    reasoning_store: ReasoningConfigStore,
    selection_store: ModelSelectionStore,
    relay: ManagedRelay | None = None,
) -> dict[str, Any]:
    managed_relay = relay or ManagedRelay()
    allowed_models, catalog_error = _managed_catalog(managed_relay)
    primary = _with_managed_availability(
        _with_reasoning(_safe_primary(), reasoning_store), allowed_models, catalog_error
    )
    cqu = _with_managed_availability(
        _with_reasoning(_safe_cqu(), reasoning_store), allowed_models, catalog_error
    )
    saved = [_with_reasoning(_safe_saved(entry), reasoning_store) for entry in store.list_models()]
    profiles = [primary, cqu, *saved]
    active_selection = _active_selection(store, selection_store)
    active_profile = next((profile for profile in profiles if profile.get("selection") == active_selection), primary)
    return {
        "primary": primary,
        "profiles": profiles,
        "activeModelId": str(active_profile.get("id") or "") or None,
        "managedCatalogError": catalog_error,
    }


def _resolve(
    store: ModelConfigStore,
    reasoning_store: ReasoningConfigStore,
    selection_store: ModelSelectionStore,
    selection: str | None,
    relay: ManagedRelay | None = None,
) -> dict[str, Any]:
    requested = str(selection or "").strip() or _active_selection(store, selection_store)
    profile = _with_reasoning(_base_profile_for_selection(store, requested), reasoning_store)

    if requested in _BUILTIN_SELECTIONS:
        managed_relay = relay or ManagedRelay()
        credential = managed_relay.credential(required=True)
        allowed_models = set(managed_relay.available_models())
        model = str(profile.get("model") or "").strip()
        if model not in allowed_models:
            raise ManagedRelayError(f"{model} is not enabled for this Loom account")
        return {
            **profile,
            "available": True,
            "availabilityReason": "",
            "provider": "openai-compatible",
            "apiKey": credential,
        }

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
    )
    return _safe_saved(entry)


def _set_active(
    store: ModelConfigStore,
    reasoning_store: ReasoningConfigStore,
    selection_store: ModelSelectionStore,
    payload: dict[str, Any],
    relay: ManagedRelay | None = None,
) -> dict[str, Any]:
    selection = str(payload.get("selection") or "").strip() or PRIMARY_SELECTION
    if selection in _BUILTIN_SELECTIONS:
        # Validate the server entitlement before persisting the built-in choice.
        _resolve(store, reasoning_store, selection_store, selection, relay=relay)
        store.set_active(None)
    else:
        saved = store.model_for_selection(selection)
        if saved is None:
            raise ValueError(f"unknown model selection: {selection!r}")
        store.set_active(saved.model_id)
    selection_store.set(selection)
    return _snapshot(store, reasoning_store, selection_store, relay=relay)


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


def _provision_managed_relay(payload: dict[str, Any], relay: ManagedRelay | None = None) -> dict[str, bool]:
    credential = str(payload.get("credential") or payload.get("apiKey") or "").strip()
    if not credential:
        raise ValueError("managed relay credential is required")
    (relay or ManagedRelay()).provision(credential)
    return {"provisioned": True}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    commands = {
        "list",
        "resolve",
        "describe-model",
        "save",
        "set-active",
        "set-reasoning",
        "provision-managed-relay",
    }
    if len(args) != 1 or args[0] not in commands:
        sys.stderr.write(
            "usage: loom_model_bridge.py {list|resolve|describe-model|save|set-active|set-reasoning|provision-managed-relay}\n"
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
        elif command == "set-reasoning":
            result = _set_reasoning(store, reasoning_store, payload)
        else:
            result = _provision_managed_relay(payload)
        _write({"ok": True, "result": result})
        return 0
    except Exception as exc:
        _write({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
