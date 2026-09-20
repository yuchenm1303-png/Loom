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

from app.ai.model_context import (
    model_context_limits_from_provider_listing,
    model_context_limits_to_camel,
)
from app.ai.model_selection_store import ModelSelectionStore
from app.ai.model_store import ModelConfigStore, StoredModel
from app.ai.profiles import ModelContextLimits
from app.ai.reasoning import ReasoningRequest
from app.ai.reasoning_catalog import resolved_reasoning
from app.ai.reasoning_store import ReasoningConfigStore
from app.ai.opencode_go_runtime import OPENCODE_GO_BASE_URL, opencode_go_protocol


PRIMARY_SELECTION = "builtin:minimax"
CQU_SELECTION = "builtin:cqu"
DEEPSEEK_SELECTION = "builtin:deepseek"
OPENCODE_GO_SELECTION_PREFIX = "builtin:opencode-go:"
MINIMAX_SELECTION_PREFIX = "builtin:minimax:"
DEEPSEEK_SELECTION_PREFIX = "builtin:deepseek:"
MANAGED_SELECTION_PREFIX = "managed:"
MANAGED_RELAY_BASE_URL = "https://relay.smirel.com/v1"
MINIMAX_BASE_URL = "https://api.minimaxi.com/v1"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MINIMAX_DEFAULT_MODEL = "MiniMax-M3"
MINIMAX_MODEL_IDS = ("MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.5")
DEEPSEEK_DEFAULT_MODEL = "deepseek-flash"
DEEPSEEK_FALLBACK_MODEL_IDS = ("deepseek-flash", "deepseek-v4-pro")
OPENCODE_GO_FALLBACK_MODEL_IDS = (
    "minimax-m3", "minimax-m2.7", "minimax-m2.5",
    "kimi-k3", "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5",
    "longcat-2.0",
    "glm-5.3-flash", "glm-5.3", "glm-5.2", "glm-5.1", "glm-5",
    "deepseek-v4-pro", "deepseek-v4.1-flash", "deepseek-v4-flash",
    "deepseek-flash", "deepseek-v4-flash-vision-exp",
    "qwen3.8-max", "qwen3.8-flash", "qwen3.7-max", "qwen3.7-plus",
    "qwen3.6-plus", "qwen3.5-plus",
    "mimo-v2.5-pro", "mimo-v2.5", "mimo-v2-pro", "mimo-v2-omni",
    "hy4-preview", "hy3", "hy3-preview",
    "gpt-5.6-luna", "grok-4.6", "grok-4.5",
    "muse-spark-1.3-contributor", "muse-spark-1.2-contributor",
    "omen-alpha",
)
CQU_DEFAULT_MODEL = "cqu-default"
# Context limits a provider published about its own models, keyed by folded
# model id and filled in as `/models` listings are fetched. Empty until a
# provider actually says something, so nothing here is ever a guess.
_DISCOVERED_CONTEXT_LIMITS: dict[str, ModelContextLimits] = {}
_KEYRING_SERVICE = "loom-agent"
_MANAGED_RELAY_CREDENTIAL_ALIAS = "managed/relay"
_DEEPSEEK_CREDENTIAL_ALIAS = "builtin/deepseek"
_OPENCODE_GO_CREDENTIAL_ALIAS = "builtin/opencode-go"
_MANAGED_RELAY_KEY_ENV = (
    "LOOM_RELAY_API_KEY",
    "SMIREL_RELAY_API_KEY",
    # Backwards-compatible aliases from the first CQU prototype. They now mean
    # "Smirel Relay customer credential", not a CQU upstream credential.
    "LOOM_CQU_API_KEY",
    "CQU_API_KEY",
)
_PRIMARY_MINIMAX_KEY_ENV = ("MINIMAX_API_KEY", "LOOM_PRIMARY_API_KEY", "LOOM_API_KEY")
_LEGACY_MINIMAX_BASE_URL_ENV = ("LOOM_MINIMAX_BASE_URL", "MINIMAX_BASE_URL")
_DEEPSEEK_KEY_ENV = ("DEEPSEEK_API_KEY", "LOOM_DEEPSEEK_API_KEY")
_DEEPSEEK_BASE_URL_ENV = ("LOOM_DEEPSEEK_BASE_URL", "DEEPSEEK_BASE_URL")
_OPENCODE_GO_KEY_ENV = ("OPENCODE_GO_API_KEY", "LOOM_OPENCODE_GO_API_KEY")
_PROVISIONING_FILE_ENV = "LOOM_RELAY_PROVISIONING_FILE"
_DEEPSEEK_DISPLAY_NAMES = {
    "deepseek-flash": "DeepSeek Flash",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
}
_MANAGED_MODEL_DISPLAY_NAMES = {
    MINIMAX_DEFAULT_MODEL.casefold(): "MiniMax",
    "minimax-m2.7": "MiniMax M2.7",
    "minimax-m2.5": "MiniMax M2.5",
    CQU_DEFAULT_MODEL.casefold(): "CQU-弘深深",
}
_MANAGED_MODEL_IDS = {
    MINIMAX_DEFAULT_MODEL.casefold(): "minimax-primary",
    "minimax-m2.7": "minimax-m27",
    "minimax-m2.5": "minimax-m25",
    CQU_DEFAULT_MODEL.casefold(): "cqu-builtin",
}
_MINIMAX_MODEL_KEYS = frozenset(model.casefold() for model in MINIMAX_MODEL_IDS)


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


def _legacy_minimax_base_url(environ: Mapping[str, str] | None = None) -> str:
    return str(
        _key_from_env(_LEGACY_MINIMAX_BASE_URL_ENV, environ) or MINIMAX_BASE_URL
    ).strip().rstrip("/")


def _deepseek_base_url(environ: Mapping[str, str] | None = None) -> str:
    return str(
        _key_from_env(_DEEPSEEK_BASE_URL_ENV, environ) or DEEPSEEK_BASE_URL
    ).strip().rstrip("/")


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
        raise RuntimeError(f"could not save the credential in the OS credential store: {exc}") from exc


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


def _is_minimax_model(model: str) -> bool:
    return str(model or "").strip().casefold() in _MINIMAX_MODEL_KEYS


def _looks_like_deepseek_connection(
    entry: StoredModel,
    environ: Mapping[str, str] | None = None,
) -> bool:
    if not isinstance(entry, StoredModel):
        return False
    endpoint = _normalize_url(_deepseek_base_url(environ))
    base_url = _normalize_url(entry.base_url)
    if base_url not in {endpoint, f"{endpoint}/v1"}:
        return False
    return str(entry.model or "").strip().casefold().startswith("deepseek-")


def _promote_saved_deepseek_key(
    store: ModelConfigStore,
    environ: Mapping[str, str] | None = None,
) -> str:
    for entry in store.list_models():
        if not _looks_like_deepseek_connection(entry, environ):
            continue
        try:
            api_key = store.secret_for(entry)
        except Exception:
            continue
        api_key = str(api_key or "").strip()
        if not api_key:
            continue
        try:
            _credential_set(_DEEPSEEK_CREDENTIAL_ALIAS, api_key)
        except RuntimeError:
            pass
        return api_key
    return ""


def _deepseek_key(
    store: ModelConfigStore,
    environ: Mapping[str, str] | None = None,
) -> str:
    secret = str(_credential_get(_DEEPSEEK_CREDENTIAL_ALIAS) or "").strip()
    if secret:
        return secret
    promoted = _promote_saved_deepseek_key(store, environ)
    if promoted:
        return promoted
    env_key = _key_from_env(_DEEPSEEK_KEY_ENV, environ)
    if env_key:
        try:
            _credential_set(_DEEPSEEK_CREDENTIAL_ALIAS, env_key)
        except RuntimeError:
            pass
        return env_key
    return ""


def _opencode_go_key(
    store: ModelConfigStore,
    environ: Mapping[str, str] | None = None,
) -> str:
    secret = str(_credential_get(_OPENCODE_GO_CREDENTIAL_ALIAS) or "").strip()
    if secret:
        return secret
    env_key = _key_from_env(_OPENCODE_GO_KEY_ENV, environ)
    if env_key:
        try:
            _credential_set(_OPENCODE_GO_CREDENTIAL_ALIAS, env_key)
        except RuntimeError:
            pass
        return env_key
    return ""


def _set_provider_key(payload: Mapping[str, Any]) -> dict[str, Any]:
    provider = str(payload.get("provider") or "").strip().casefold()
    api_key = str(payload.get("apiKey") or payload.get("api_key") or "").strip()
    if provider != "opencode-go":
        raise ValueError("only opencode-go built-in credentials are configurable here")
    if not api_key:
        raise ValueError("API key must not be empty")
    _credential_set(_OPENCODE_GO_CREDENTIAL_ALIAS, api_key)
    return {"provider": provider, "configured": True}


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


def _fetch_opencode_go_model_ids(timeout: float = 3.5) -> list[str]:
    request = urllib.request.Request(
        f"{OPENCODE_GO_BASE_URL}/models",
        headers={"Accept": "application/json", "User-Agent": "Loom/0.1 (coding-agent)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        folded = model_id.casefold()
        if not model_id or folded in seen:
            continue
        seen.add(folded)
        result.append(model_id)
    return result


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
        # The provider is the only authority on its own window that does not
        # require guessing. Remember whatever it publishes; anything it omits
        # stays undeclared rather than invented.
        limits = model_context_limits_from_provider_listing(item)
        if limits.context_window_tokens or limits.output_reserve_tokens:
            _DISCOVERED_CONTEXT_LIMITS[key] = limits
    return models


def _deepseek_models_url(environ: Mapping[str, str] | None = None) -> str:
    return f"{_deepseek_base_url(environ)}/models"


def _fetch_deepseek_model_ids(
    api_key: str,
    environ: Mapping[str, str] | None = None,
    timeout: float = 3.5,
) -> list[str]:
    api_key = str(api_key or "").strip()
    if not api_key:
        return []
    request = urllib.request.Request(
        _deepseek_models_url(environ),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "Loom/deepseek",
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
        # The provider is the only authority on its own window that does not
        # require guessing. Remember whatever it publishes; anything it omits
        # stays undeclared rather than invented.
        limits = model_context_limits_from_provider_listing(item)
        if limits.context_window_tokens or limits.output_reserve_tokens:
            _DISCOVERED_CONTEXT_LIMITS[key] = limits
    return models


def _minimax_selection_for_model(model: str) -> str:
    normalized = str(model or "").strip()
    if normalized.casefold() == MINIMAX_DEFAULT_MODEL.casefold():
        return PRIMARY_SELECTION
    return f"{MINIMAX_SELECTION_PREFIX}{urllib.parse.quote(normalized, safe='')}"


def _minimax_model_from_selection(selection: str) -> str | None:
    value = str(selection or "").strip()
    if value == PRIMARY_SELECTION:
        return MINIMAX_DEFAULT_MODEL
    if value.startswith(MINIMAX_SELECTION_PREFIX):
        model = urllib.parse.unquote(value[len(MINIMAX_SELECTION_PREFIX) :]).strip()
        return model if _is_minimax_model(model) else None
    return None


def _deepseek_selection_for_model(model: str) -> str:
    normalized = str(model or "").strip()
    if normalized.casefold() == DEEPSEEK_DEFAULT_MODEL.casefold():
        return DEEPSEEK_SELECTION
    return f"{DEEPSEEK_SELECTION_PREFIX}{urllib.parse.quote(normalized, safe='')}"


def _deepseek_model_from_selection(selection: str) -> str | None:
    value = str(selection or "").strip()
    if value == DEEPSEEK_SELECTION:
        return DEEPSEEK_DEFAULT_MODEL
    if value.startswith(DEEPSEEK_SELECTION_PREFIX):
        model = urllib.parse.unquote(value[len(DEEPSEEK_SELECTION_PREFIX) :]).strip()
        return model or None
    return None


def _opencode_go_selection_for_model(model: str) -> str:
    normalized = str(model or "").strip()
    if not normalized:
        raise ValueError("OpenCode Go model id must not be empty")
    return f"{OPENCODE_GO_SELECTION_PREFIX}{urllib.parse.quote(normalized, safe='')}"


def _opencode_go_model_from_selection(selection: str) -> str | None:
    value = str(selection or "").strip()
    if not value.startswith(OPENCODE_GO_SELECTION_PREFIX):
        return None
    model = urllib.parse.unquote(value[len(OPENCODE_GO_SELECTION_PREFIX) :]).strip()
    return model or None


def _opencode_go_family(model: str) -> str:
    value = str(model or "").strip().casefold()
    for prefix, family in (
        ("gpt-", "GPT"),
        ("grok-", "Grok"),
        ("deepseek-", "DeepSeek"),
        ("glm-", "GLM"),
        ("kimi-", "Kimi"),
        ("minimax-", "MiniMax"),
        ("qwen", "Qwen"),
        ("mimo-", "MiMo"),
        ("muse-", "Muse"),
        ("hy", "Hunyuan"),
        ("longcat-", "LongCat"),
    ):
        if value.startswith(prefix):
            return family
    return "Other"


def _opencode_go_display_name(model: str) -> str:
    value = str(model or "").strip()
    special = {
        "gpt-5.6-luna": "GPT 5.6 Luna",
        "grok-4.6": "Grok 4.6",
        "grok-4.5": "Grok 4.5",
        "deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
        "deepseek-v4-pro": "DeepSeek V4 Pro",
        "deepseek-v4-flash": "DeepSeek V4 Flash",
        "deepseek-flash": "DeepSeek Flash",
        "deepseek-v4-flash-vision-exp": "DeepSeek V4 Flash Vision Exp",
        "longcat-2.0": "LongCat 2.0",
        "omen-alpha": "Omen Alpha",
    }
    if value.casefold() in special:
        return special[value.casefold()]
    return value.replace("-", " ").title().replace("Qwen3.", "Qwen 3.").replace("Glm ", "GLM ").replace("Mimo ", "MiMo ")


def _canonical_builtin_selection(selection: str, model: str) -> str:
    """Keep built-in model identity and provider routing inseparable.

    "Other model ID" intentionally keeps the current connection for saved and
    managed endpoints. Built-in MiniMax/DeepSeek profiles are different: their
    credentials and base URLs are provider-owned. If a known built-in model is
    entered while another built-in provider is active, carry the provider
    selection with the model instead of manufacturing a mixed identity such as
    builtin:minimax + deepseek-flash.
    """

    current = str(selection or "").strip()
    requested = str(model or "").strip()
    if not requested:
        return current

    is_provider_builtin = (
        current == PRIMARY_SELECTION
        or current.startswith(MINIMAX_SELECTION_PREFIX)
        or current == DEEPSEEK_SELECTION
        or current.startswith(DEEPSEEK_SELECTION_PREFIX)
        or current.startswith(OPENCODE_GO_SELECTION_PREFIX)
        or current == CQU_SELECTION
    )
    if not is_provider_builtin:
        return current

    if current.startswith(OPENCODE_GO_SELECTION_PREFIX):
        return _opencode_go_selection_for_model(requested)
    if _is_minimax_model(requested):
        return _minimax_selection_for_model(requested)
    if requested.casefold().startswith("deepseek-"):
        return _deepseek_selection_for_model(requested)
    if requested.casefold() == CQU_DEFAULT_MODEL.casefold():
        return CQU_SELECTION
    return current


def _managed_selection_for_model(model: str) -> str:
    normalized = str(model or "").strip()
    folded = normalized.casefold()
    if folded == CQU_DEFAULT_MODEL.casefold():
        return CQU_SELECTION
    return f"{MANAGED_SELECTION_PREFIX}{urllib.parse.quote(normalized, safe='')}"


def _managed_model_from_selection(selection: str) -> str | None:
    value = str(selection or "").strip()
    if value == CQU_SELECTION:
        return CQU_DEFAULT_MODEL
    if value.startswith(MANAGED_SELECTION_PREFIX):
        model = urllib.parse.unquote(value[len(MANAGED_SELECTION_PREFIX) :]).strip()
        return model or None
    return None


def _deepseek_profile_id(model: str) -> str:
    folded = str(model or "").strip().casefold()
    if folded == "deepseek-flash":
        return "deepseek-flash"
    if folded == "deepseek-v4-pro":
        return "deepseek-v4-pro"
    digest = hashlib.sha256(folded.encode("utf-8")).hexdigest()[:12]
    return f"deepseek-{digest}"


def _deepseek_display_name(model: str) -> str:
    value = str(model or "").strip()
    known = _DEEPSEEK_DISPLAY_NAMES.get(value.casefold())
    if known:
        return known
    return value


def _managed_profile_id(model: str) -> str:
    folded = str(model or "").strip().casefold()
    if folded in _MANAGED_MODEL_IDS:
        return _MANAGED_MODEL_IDS[folded]
    digest = hashlib.sha256(folded.encode("utf-8")).hexdigest()[:12]
    return f"managed-{digest}"


def _managed_display_name(model: str) -> str:
    value = str(model or "").strip()
    return _MANAGED_MODEL_DISPLAY_NAMES.get(value.casefold(), value)


def _safe_minimax(
    model: str = MINIMAX_DEFAULT_MODEL,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    model = str(model or "").strip()
    if not _is_minimax_model(model):
        raise ValueError(f"unsupported MiniMax model id: {model!r}")
    return {
        "selection": _minimax_selection_for_model(model),
        "id": _managed_profile_id(model),
        "kind": "builtin",
        "name": _managed_display_name(model),
        "groupId": "minimax",
        "groupName": "MiniMax",
        "groupOrder": 10,
        "adapter": "openai-compatible",
        "baseUrl": _legacy_minimax_base_url(environ),
        "model": model,
    }


def _discovered_context_limits(model: str) -> dict[str, Any] | None:
    """Camel-cased limits this provider published for ``model``, if any."""
    limits = _DISCOVERED_CONTEXT_LIMITS.get(str(model or "").strip().casefold())
    if limits is None:
        return None
    payload = {
        key: value
        for key, value in model_context_limits_to_camel(limits).items()
        if value is not None
    }
    return payload or None


def _with_discovered_limits(profile: dict[str, Any]) -> dict[str, Any]:
    """Attach published limits so the runtime binds a real window, not a guess."""
    limits = _discovered_context_limits(profile.get("model", ""))
    if limits is None:
        return profile
    return {**profile, "contextLimits": limits}


def _safe_deepseek(
    model: str = DEEPSEEK_DEFAULT_MODEL,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    model = str(model or "").strip()
    if not model:
        raise ValueError("DeepSeek model id must not be empty")
    return {
        "selection": _deepseek_selection_for_model(model),
        "id": _deepseek_profile_id(model),
        "kind": "builtin",
        "name": _deepseek_display_name(model),
        "groupId": "deepseek",
        "groupName": "DeepSeek",
        "groupOrder": 20,
        "adapter": "openai-compatible",
        "baseUrl": _deepseek_base_url(environ),
        "model": model,
    }


def _safe_managed(model: str, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    model = str(model or "").strip()
    if not model:
        raise ValueError("managed model id must not be empty")
    if _is_minimax_model(model):
        return _safe_minimax(model, environ)
    return {
        "selection": _managed_selection_for_model(model),
        "id": _managed_profile_id(model),
        "kind": "builtin",
        "name": _managed_display_name(model),
        "groupId": "managed-relay",
        "groupName": "Managed models",
        "groupOrder": 40,
        "adapter": "openai-compatible",
        "baseUrl": _managed_relay_base_url(environ),
        "model": model,
    }


def _safe_opencode_go(model: str, *, configured: bool) -> dict[str, Any]:
    model = str(model or "").strip()
    if not model:
        raise ValueError("OpenCode Go model id must not be empty")
    vision = model.casefold() in {"deepseek-v4-flash-vision-exp", "mimo-v2-omni"}
    return {
        "selection": _opencode_go_selection_for_model(model),
        "id": "opencode-go-" + hashlib.sha256(model.casefold().encode("utf-8")).hexdigest()[:12],
        "kind": "builtin",
        "name": _opencode_go_display_name(model),
        "groupId": "opencode-go",
        "groupName": "OpenCode Go",
        "groupOrder": 30,
        "family": _opencode_go_family(model),
        "protocol": opencode_go_protocol(model),
        "configured": bool(configured),
        "adapter": "opencode-go",
        "baseUrl": OPENCODE_GO_BASE_URL,
        "model": model,
        "vision": vision,
    }


def _safe_primary() -> dict[str, Any]:
    return _safe_minimax(MINIMAX_DEFAULT_MODEL)


def _safe_cqu() -> dict[str, Any]:
    return _safe_managed(CQU_DEFAULT_MODEL)


def _safe_legacy_minimax(
    model: str = MINIMAX_DEFAULT_MODEL,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return _safe_minimax(model, environ)


def _safe_saved(entry: StoredModel) -> dict[str, Any]:
    return {
        "selection": entry.selection,
        "id": entry.model_id,
        "kind": "saved",
        "name": entry.display_name,
        "groupId": f"saved:{entry.model_id}",
        "groupName": "Custom APIs",
        "groupOrder": 100,
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
    profiles: list[dict[str, Any]] = [_safe_minimax(model_id, environ) for model_id in MINIMAX_MODEL_IDS]

    deepseek_key = _deepseek_key(store, environ)
    deepseek_model_ids = list(DEEPSEEK_FALLBACK_MODEL_IDS)
    if deepseek_key:
        discovered = _fetch_deepseek_model_ids(deepseek_key, environ)
        if discovered:
            deepseek_model_ids = discovered
    seen_deepseek: set[str] = set()
    for model_id in deepseek_model_ids:
        folded = str(model_id or "").strip().casefold()
        if not folded or folded in seen_deepseek:
            continue
        seen_deepseek.add(folded)
        profiles.append(_with_discovered_limits(_safe_deepseek(model_id, environ)))

    opencode_key = _opencode_go_key(store, environ)
    opencode_model_ids = _fetch_opencode_go_model_ids() or list(OPENCODE_GO_FALLBACK_MODEL_IDS)
    seen_opencode: set[str] = set()
    for model_id in opencode_model_ids:
        folded = str(model_id or "").strip().casefold()
        if not folded or folded in seen_opencode:
            continue
        seen_opencode.add(folded)
        profiles.append(_safe_opencode_go(model_id, configured=bool(opencode_key)))

    api_key = _managed_relay_key(store, environ, Path(__file__).resolve().parent)
    if api_key:
        model_ids = _fetch_managed_model_ids(api_key, environ)
        if not model_ids:
            model_ids = [CQU_DEFAULT_MODEL]
        seen = {str(profile.get("model") or "").strip().casefold() for profile in profiles}
        for model_id in model_ids:
            folded = str(model_id or "").strip().casefold()
            if not folded or folded in seen or _is_minimax_model(model_id):
                continue
            seen.add(folded)
            profiles.append(_with_discovered_limits(_safe_managed(model_id, environ)))
    return profiles


def _base_profile_for_selection(store: ModelConfigStore, selection: str) -> dict[str, Any]:
    requested = str(selection or "").strip()
    minimax_model = _minimax_model_from_selection(requested)
    if minimax_model:
        return _safe_minimax(minimax_model)
    deepseek_model = _deepseek_model_from_selection(requested)
    if deepseek_model:
        return _safe_deepseek(deepseek_model)
    opencode_model = _opencode_go_model_from_selection(requested)
    if opencode_model:
        return _safe_opencode_go(opencode_model, configured=bool(_opencode_go_key(store)))
    opencode_model = _opencode_go_model_from_selection(requested)
    if opencode_model:
        api_key = _opencode_go_key(store)
        if api_key:
            opencode_profile = _with_reasoning(
                _safe_opencode_go(opencode_model, configured=True),
                reasoning_store,
            )
            return {**opencode_profile, "provider": "opencode-go", "apiKey": api_key}
        raise RuntimeError(
            "OpenCode Go API key is not configured. Open the OpenCode Go model group "
            "in Loom and connect your subscription key."
        )

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
    effective_selection = _canonical_builtin_selection(selection, requested_model)
    profile = _base_profile_for_selection(store, effective_selection)
    if _is_minimax_model(requested_model):
        profile = _safe_minimax(requested_model)
    elif _deepseek_model_from_selection(effective_selection):
        profile = _safe_deepseek(requested_model)
    elif _opencode_go_model_from_selection(effective_selection):
        profile = _safe_opencode_go(requested_model, configured=bool(_opencode_go_key(store)))
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

    minimax_model = _minimax_model_from_selection(requested)
    if minimax_model:
        api_key = _primary_minimax_key()
        if api_key:
            official_profile = _with_reasoning(
                _with_discovered_limits(_safe_minimax(minimax_model)), reasoning_store
            )
            return {**official_profile, "provider": "openai-compatible", "apiKey": api_key}
        raise RuntimeError(
            "MiniMax API key is not configured. Set MINIMAX_API_KEY for the official "
            "MiniMax endpoint, or add a saved MiniMax connection."
        )

    deepseek_model = _deepseek_model_from_selection(requested)
    if deepseek_model:
        api_key = _deepseek_key(store)
        if api_key:
            official_profile = _with_reasoning(
                _with_discovered_limits(_safe_deepseek(deepseek_model)), reasoning_store
            )
            return {**official_profile, "provider": "openai-compatible", "apiKey": api_key}
        raise RuntimeError(
            "DeepSeek API key is not configured. Set DEEPSEEK_API_KEY once or add a saved "
            "DeepSeek connection using the official https://api.deepseek.com endpoint."
        )

    managed_model = _managed_model_from_selection(requested)
    if managed_model:
        api_key = _managed_relay_key(store, repo_root=Path(__file__).resolve().parent)
        if api_key:
            return {**profile, "provider": "openai-compatible", "apiKey": api_key}
        raise RuntimeError(
            "Smirel Relay credential is not provisioned for CQUAI. Build or install Loom with "
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


def resolve_model_spec(
    selection: str,
    *,
    model: str = "",
    home: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve one thread model without allowing a built-in provider/model split."""

    store = ModelConfigStore(home)
    reasoning_store = ReasoningConfigStore(store.home)
    selection_store = ModelSelectionStore(store.home)
    requested_model = str(model or "").strip()
    effective_selection = (
        _canonical_builtin_selection(selection, requested_model)
        if requested_model
        else str(selection or "").strip()
    )
    resolved = _resolve(store, reasoning_store, selection_store, effective_selection)
    if requested_model and requested_model != str(resolved.get("model") or ""):
        described = _describe_model(store, reasoning_store, effective_selection, requested_model)
        resolved = {
            **resolved,
            "selection": str(described.get("selection") or resolved.get("selection") or effective_selection),
            "id": str(described.get("id") or resolved.get("id") or ""),
            "name": str(described.get("name") or resolved.get("name") or ""),
            "adapter": str(described.get("adapter") or resolved.get("adapter") or ""),
            "baseUrl": str(described.get("baseUrl") or resolved.get("baseUrl") or ""),
            "model": requested_model,
            "reasoning": described.get("reasoning"),
        }
    return resolved


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


def _delete(
    store: ModelConfigStore,
    reasoning_store: ReasoningConfigStore,
    selection_store: ModelSelectionStore,
    payload: dict[str, Any],
) -> dict[str, Any]:
    selection = str(payload.get("selection") or "").strip()
    saved = store.model_for_selection(selection)
    if saved is None:
        raise ValueError("only saved model connections can be deleted")
    removed = store.delete_model(saved.model_id)
    if selection_store.get() == removed.selection:
        selection_store.set(PRIMARY_SELECTION)
    return _snapshot(store, reasoning_store, selection_store)


def _persist_active(
    store: ModelConfigStore,
    selection_store: ModelSelectionStore,
    payload: dict[str, Any],
) -> dict[str, Any]:
    selection = str(payload.get("selection") or "").strip() or PRIMARY_SELECTION
    if (
        _minimax_model_from_selection(selection)
        or _deepseek_model_from_selection(selection)
        or _opencode_go_model_from_selection(selection)
        or _managed_model_from_selection(selection)
    ):
        store.set_active(None)
    else:
        saved = store.model_for_selection(selection)
        if saved is None:
            raise ValueError(f"unknown model selection: {selection!r}")
        store.set_active(saved.model_id)
    selection_store.set(selection)
    return {"selection": selection}


def _set_active(
    store: ModelConfigStore,
    reasoning_store: ReasoningConfigStore,
    selection_store: ModelSelectionStore,
    payload: dict[str, Any],
) -> dict[str, Any]:
    _persist_active(store, selection_store, payload)
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
    commands = {"list", "resolve", "describe-model", "save", "delete", "set-active", "persist-active", "set-reasoning", "set-provider-key"}
    if len(args) != 1 or args[0] not in commands:
        sys.stderr.write(
            "usage: loom_model_bridge.py {list|resolve|describe-model|save|delete|set-active|persist-active|set-reasoning|set-provider-key}\n"
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
        elif command == "delete":
            result = _delete(store, reasoning_store, selection_store, payload)
        elif command == "set-active":
            result = _set_active(store, reasoning_store, selection_store, payload)
        elif command == "persist-active":
            result = _persist_active(store, selection_store, payload)
        elif command == "set-provider-key":
            result = _set_provider_key(payload)
        else:
            result = _set_reasoning(store, reasoning_store, payload)
        _write({"ok": True, "result": result})
        return 0
    except Exception as exc:
        _write({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
