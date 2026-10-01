from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.ai.model_context import model_context_limits_from_provider_listing, model_context_limits_to_camel
from app.ai.model_selection_store import ModelSelectionStore
from app.ai.reasoning import ReasoningRequest
from app.ai.reasoning_catalog import resolved_reasoning
from app.ai.reasoning_store import ReasoningConfigStore


ANT_LING_SELECTION = "builtin:ant-ling"
ANT_LING_SELECTION_PREFIX = "builtin:ant-ling:"
ANT_LING_BASE_URL = "https://api.ant-ling.com/v1"
ANT_LING_DEFAULT_MODEL = "Ling-3.0-flash"
ANT_LING_FALLBACK_MODEL_IDS = (
    "Ling-3.0-flash",
    "Ling-3.0-flash-VL",
    "Ling-3.0-tiny",
    "Ling-2.6-1T",
    "Ring-2.6-1T",
    "Ling-2.6-flash",
)
_KEYRING_SERVICE = "loom-agent"
_CREDENTIAL_ALIAS = "builtin/ant-ling"
_KEY_ENV = ("ANT_LING_API_KEY", "LOOM_ANT_LING_API_KEY")
_DISPLAY_NAMES = {
    "ling-3.0-flash": "Ling 3.0 Flash",
    "ling-3.0-flash-vl": "Ling 3.0 Flash VL",
    "ling-3.0-tiny": "Ling 3.0 Tiny",
    "ling-2.6-1t": "Ling 2.6 1T",
    "ring-2.6-1t": "Ring 2.6 1T",
    "ling-2.6-flash": "Ling 2.6 Flash",
}
_DISCOVERED_LIMITS: dict[str, dict[str, int]] = {}


def _home() -> Path:
    raw = str(os.environ.get("LOOM_HOME") or "").strip()
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".loom").resolve()


def _credential_get() -> str:
    try:
        import keyring

        return str(keyring.get_password(_KEYRING_SERVICE, _CREDENTIAL_ALIAS) or "").strip()
    except Exception:
        return ""


def _credential_set(value: str) -> None:
    try:
        import keyring

        keyring.set_password(_KEYRING_SERVICE, _CREDENTIAL_ALIAS, value)
    except Exception as exc:
        raise RuntimeError(f"could not save the credential in the OS credential store: {exc}") from exc


def _api_key() -> str:
    secret = _credential_get()
    if secret:
        return secret
    for name in _KEY_ENV:
        value = str(os.environ.get(name) or "").strip()
        if not value:
            continue
        try:
            _credential_set(value)
        except RuntimeError:
            pass
        return value
    return ""


def _selection_for_model(model: str) -> str:
    normalized = str(model or "").strip()
    if not normalized:
        raise ValueError("Ant Ling model id must not be empty")
    if normalized.casefold() == ANT_LING_DEFAULT_MODEL.casefold():
        return ANT_LING_SELECTION
    return f"{ANT_LING_SELECTION_PREFIX}{urllib.parse.quote(normalized, safe='')}"


def _model_from_selection(selection: str) -> str | None:
    value = str(selection or "").strip()
    if value == ANT_LING_SELECTION:
        return ANT_LING_DEFAULT_MODEL
    if value.startswith(ANT_LING_SELECTION_PREFIX):
        model = urllib.parse.unquote(value[len(ANT_LING_SELECTION_PREFIX) :]).strip()
        return model or None
    return None


def _display_name(model: str) -> str:
    value = str(model or "").strip()
    return _DISPLAY_NAMES.get(value.casefold(), value)


def _profile_id(model: str) -> str:
    digest = hashlib.sha256(str(model or "").strip().casefold().encode("utf-8")).hexdigest()[:12]
    return f"ant-ling-{digest}"


def _reasoning_key(selection: str, model: str) -> str:
    return f"{selection}::{str(model or '').strip().casefold()}"


def _profile(
    model: str,
    *,
    configured: bool,
    source: str,
    reasoning_store: ReasoningConfigStore,
) -> dict[str, Any]:
    model = str(model or "").strip()
    selection = _selection_for_model(model)
    capability, selected = resolved_reasoning(
        model=model,
        adapter="openai-compatible",
        base_url=ANT_LING_BASE_URL,
        saved=reasoning_store.get(_reasoning_key(selection, model)) or reasoning_store.get(selection),
    )
    payload: dict[str, Any] = {
        "selection": selection,
        "id": _profile_id(model),
        "kind": "builtin",
        "name": _display_name(model),
        "groupId": "ant-ling",
        "groupName": "Ant Ling",
        "groupOrder": 25,
        "adapter": "openai-compatible",
        "baseUrl": ANT_LING_BASE_URL,
        "model": model,
        "vision": model.casefold().endswith("-vl"),
        "configured": bool(configured),
        "available": True,
        "catalogSource": source,
    }
    limits = _DISCOVERED_LIMITS.get(model.casefold())
    if limits:
        payload["contextLimits"] = dict(limits)
    if capability is not None and selected is not None:
        payload["reasoning"] = {**capability, "value": selected.value}
    else:
        payload["reasoning"] = None
    return payload


def _fetch_models(api_key: str, timeout: float = 3.5) -> tuple[list[str], bool]:
    request = urllib.request.Request(
        f"{ANT_LING_BASE_URL}/models",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "Loom/ant-ling",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return [], exc.code in {401, 403}
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return [], False
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return [], False

    models: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        folded = model_id.casefold()
        if not model_id or folded in seen:
            continue
        seen.add(folded)
        models.append(model_id)
        limits = model_context_limits_from_provider_listing(item)
        mapped = {
            key: value
            for key, value in model_context_limits_to_camel(limits).items()
            if value is not None
        }
        if mapped:
            _DISCOVERED_LIMITS[folded] = mapped
    return models, False


def _registry() -> dict[str, Any]:
    home = _home()
    reasoning_store = ReasoningConfigStore(home)
    selection_store = ModelSelectionStore(home)
    key = _api_key()
    discovered: list[str] = []
    if key:
        # The OpenAI-compatible chat endpoint is the runtime contract. Model
        # discovery is optional metadata and must never turn an already stored
        # credential back into a disconnected UI state.
        discovered, _ = _fetch_models(key)
    model_ids = discovered or list(ANT_LING_FALLBACK_MODEL_IDS)
    configured = bool(key)
    source = "provider" if discovered else "fallback"
    profiles = [
        _profile(model, configured=configured, source=source, reasoning_store=reasoning_store)
        for model in model_ids
    ]
    active = str(selection_store.get() or "").strip()
    return {
        "profiles": profiles,
        "activeSelection": active if _model_from_selection(active) else None,
    }


def _resolve(selection: str) -> dict[str, Any]:
    model = _model_from_selection(selection)
    if not model:
        raise ValueError(f"unknown Ant Ling selection: {selection!r}")
    key = _api_key()
    if not key:
        raise RuntimeError("Ant Ling API key is not configured. Open the Ant Ling model group and connect it first.")
    reasoning_store = ReasoningConfigStore(_home())
    profile = _profile(model, configured=True, source="runtime", reasoning_store=reasoning_store)
    return {**profile, "provider": "openai-compatible", "apiKey": key}


def _describe(selection: str, model: str) -> dict[str, Any]:
    if not _model_from_selection(selection):
        raise ValueError(f"unknown Ant Ling selection: {selection!r}")
    reasoning_store = ReasoningConfigStore(_home())
    return _profile(
        model,
        configured=bool(_api_key()),
        source="runtime",
        reasoning_store=reasoning_store,
    )


def _set_reasoning(selection: str, model: str, kind: str, value: str) -> dict[str, Any]:
    if not _model_from_selection(selection):
        raise ValueError(f"unknown Ant Ling selection: {selection!r}")
    home = _home()
    reasoning_store = ReasoningConfigStore(home)
    profile = _profile(model, configured=bool(_api_key()), source="runtime", reasoning_store=reasoning_store)
    capability = profile.get("reasoning")
    if not isinstance(capability, dict):
        raise ValueError("the selected model does not advertise reasoning controls")
    requested = ReasoningRequest.from_values(kind, value)
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
    return _profile(model, configured=bool(_api_key()), source="runtime", reasoning_store=reasoning_store)


def _read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Ant Ling bridge input must be a JSON object")
    return payload


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    commands = {"list", "resolve", "describe-model", "set-key", "set-active", "set-reasoning"}
    if len(args) != 1 or args[0] not in commands:
        _write({"ok": False, "error": "unsupported Ant Ling bridge command"})
        return 2
    command = args[0]
    payload = _read_payload()
    try:
        if command == "list":
            result = _registry()
        elif command == "resolve":
            result = _resolve(str(payload.get("selection") or ""))
        elif command == "describe-model":
            result = _describe(str(payload.get("selection") or ""), str(payload.get("model") or ""))
        elif command == "set-key":
            api_key = str(payload.get("apiKey") or "").strip()
            if not api_key:
                raise ValueError("API key must not be empty")
            _credential_set(api_key)
            result = {"configured": True}
        elif command == "set-active":
            selection = str(payload.get("selection") or "").strip()
            if not _model_from_selection(selection):
                raise ValueError(f"unknown Ant Ling selection: {selection!r}")
            ModelSelectionStore(_home()).set(selection)
            result = {"selection": selection}
        else:
            result = _set_reasoning(
                str(payload.get("selection") or ""),
                str(payload.get("model") or ""),
                str(payload.get("kind") or ""),
                str(payload.get("value") or ""),
            )
        _write({"ok": True, "result": result})
        return 0
    except Exception as exc:
        _write({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
