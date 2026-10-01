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
from app.ai.model_store import ModelConfigStore
from app.ai.reasoning import ReasoningRequest
from app.ai.reasoning_catalog import resolved_reasoning
from app.ai.reasoning_store import ReasoningConfigStore
from loom_model_bridge import _managed_relay_base_url, _managed_relay_key


ANT_LING_SELECTION = "builtin:ant-ling"
ANT_LING_SELECTION_PREFIX = "builtin:ant-ling:"
ANT_LING_DEFAULT_MODEL = "Ling-3.0-flash"
ANT_LING_FALLBACK_MODEL_IDS = (
    "Ling-3.0-flash",
    "Ling-3.0-flash-VL",
    "Ling-3.0-tiny",
    "Ling-2.6-1T",
    "Ring-2.6-1T",
    "Ling-2.6-flash",
)
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


def _relay_key() -> str:
    """Return Loom's customer/device Relay credential, never an Ant Ling key.

    The Ant Ling upstream credential stays on Muxway. The desktop only owns a
    Relay credential whose server-side group decides whether Ling/Ring models
    are callable for this installation/user package.
    """

    store = ModelConfigStore(_home())
    return str(
        _managed_relay_key(
            store,
            repo_root=Path(__file__).resolve().parent,
        )
        or ""
    ).strip()


def _relay_base_url() -> str:
    return _managed_relay_base_url()


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


def _is_ant_ling_model(model: str) -> bool:
    folded = str(model or "").strip().casefold()
    return folded.startswith("ling-") or folded.startswith("ring-")


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
    source: str,
    reasoning_store: ReasoningConfigStore,
) -> dict[str, Any]:
    model = str(model or "").strip()
    if not _is_ant_ling_model(model):
        raise ValueError(f"not an Ant Ling model id: {model!r}")
    selection = _selection_for_model(model)
    base_url = _relay_base_url()
    capability, selected = resolved_reasoning(
        model=model,
        adapter="openai-compatible",
        base_url=base_url,
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
        "baseUrl": base_url,
        "model": model,
        "vision": model.casefold().endswith("-vl"),
        "configured": True,
        "available": True,
        "catalogSource": source,
        "managed": True,
    }
    limits = _DISCOVERED_LIMITS.get(model.casefold())
    if limits:
        payload["contextLimits"] = dict(limits)
    if capability is not None and selected is not None:
        payload["reasoning"] = {**capability, "value": selected.value}
    else:
        payload["reasoning"] = None
    return payload


def _access_status_profile(message: str) -> dict[str, Any]:
    return {
        "selection": f"{ANT_LING_SELECTION_PREFIX}__managed_access_status__",
        "id": "ant-ling-managed-access-status",
        "kind": "builtin",
        "name": "Managed access",
        "groupId": "ant-ling",
        "groupName": "Ant Ling",
        "groupOrder": 25,
        "adapter": "openai-compatible",
        "baseUrl": _relay_base_url(),
        "model": "",
        "vision": False,
        "configured": True,
        "available": False,
        "catalogSource": "fallback",
        "managed": True,
        "setupOnly": True,
        "statusMessage": message,
    }


def _fetch_models(relay_key: str, timeout: float = 3.5) -> list[str]:
    """Read the authoritative Ant Ling entitlement from Muxway `/models`.

    The Relay is authoritative for inference access. The local fallback catalog
    is display-only: it keeps the built-in Ant Ling provider visible when an
    account has not been granted access yet, but those rows remain disabled and
    cannot bypass server-side entitlement checks.
    """

    secret = str(relay_key or "").strip()
    if not secret:
        return []
    request = urllib.request.Request(
        f"{_relay_base_url()}/models",
        headers={
            "Authorization": f"Bearer {secret}",
            "Accept": "application/json",
            "User-Agent": "Loom/ant-ling-managed",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []

    models: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        folded = model_id.casefold()
        if not model_id or folded in seen or not _is_ant_ling_model(model_id):
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
    return models


def _registry() -> dict[str, Any]:
    home = _home()
    reasoning_store = ReasoningConfigStore(home)
    selection_store = ModelSelectionStore(home)
    relay_key = _relay_key()
    model_ids = _fetch_models(relay_key) if relay_key else []

    if model_ids:
        profiles = [
            _profile(model, source="provider", reasoning_store=reasoning_store)
            for model in model_ids
        ]
    else:
        message = (
            "Ant Ling is a Loom built-in provider, but this Loom account/device has not been granted managed Ant Ling access yet. "
            "An administrator can enable it on the Relay; no Ant Ling API key is required here."
        )
        profiles = [
            {
                **_profile(model, source="fallback", reasoning_store=reasoning_store),
                "available": False,
                "statusMessage": message,
            }
            for model in ANT_LING_FALLBACK_MODEL_IDS
        ]
        profiles.append(_access_status_profile(message))

    active = str(selection_store.get() or "").strip()
    active_is_available = any(
        profile.get("selection") == active and profile.get("available") is not False
        for profile in profiles
    )
    return {
        "profiles": profiles,
        "activeSelection": active if active_is_available and _model_from_selection(active) else None,
    }


def _resolve(selection: str) -> dict[str, Any]:
    model = _model_from_selection(selection)
    if not model or not _is_ant_ling_model(model):
        raise ValueError(f"unknown Ant Ling selection: {selection!r}")
    relay_key = _relay_key()
    if not relay_key:
        raise RuntimeError(
            "Loom managed-model access is not provisioned. Sign in/use a Loom package with built-in model access, "
            "or add your own Ant Ling API under Add connection."
        )
    entitled = {item.casefold() for item in _fetch_models(relay_key)}
    if model.casefold() not in entitled:
        raise RuntimeError(
            "Ant Ling built-in access is not enabled for this Loom account/device. "
            "Enable the model in the Loom/Muxway admin policy, or use Add connection with your own Ant Ling key."
        )
    reasoning_store = ReasoningConfigStore(_home())
    profile = _profile(model, source="runtime", reasoning_store=reasoning_store)
    return {**profile, "provider": "openai-compatible", "apiKey": relay_key}


def _describe(selection: str, model: str) -> dict[str, Any]:
    if not _model_from_selection(selection):
        raise ValueError(f"unknown Ant Ling selection: {selection!r}")
    reasoning_store = ReasoningConfigStore(_home())
    return _profile(model, source="runtime", reasoning_store=reasoning_store)


def _set_reasoning(selection: str, model: str, kind: str, value: str) -> dict[str, Any]:
    if not _model_from_selection(selection):
        raise ValueError(f"unknown Ant Ling selection: {selection!r}")
    home = _home()
    reasoning_store = ReasoningConfigStore(home)
    profile = _profile(model, source="runtime", reasoning_store=reasoning_store)
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
    return _profile(model, source="runtime", reasoning_store=reasoning_store)


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
            raise RuntimeError(
                "Built-in Ant Ling is managed by Loom/Muxway and does not accept an upstream key here. "
                "Use Add connection when you want to call Ant Ling with your own API key."
            )
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
