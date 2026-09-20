from __future__ import annotations

from typing import Any, Mapping

from app.ai import (
    AGENT_FAST_ROLE,
    AIConfiguration,
    CredentialRef,
    CredentialResolver,
    ModelBinding,
    ModelCapability,
    ModelContextLimits,
    ProviderAdapter,
    ProviderConnection,
    ReasoningRequest,
    build_ai_platform,
)
from app.ai.model_context import model_context_limits_from_env, model_context_limits_from_mapping
from app.ai.model_store import ModelConfigStore
from app.ai.reasoning_catalog import reasoning_capability
from app.agent_runtime.computer_transient import ComputerTransientInputPlatform

_RUNTIME_KEY_ALIAS = "loom-api-key"
_PROVIDER_ID = "loom-primary"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_base_url(adapter: ProviderAdapter, value: str) -> str:
    if adapter in {ProviderAdapter.OPENAI, ProviderAdapter.OPENCODE_GO}:
        return ""
    return _text(value).rstrip("/")


def _has_authoritative_context_limits(limits: ModelContextLimits) -> bool:
    return any(
        value is not None
        for value in (
            limits.context_window_tokens,
            limits.auto_compact_token_limit,
            limits.output_reserve_tokens,
            limits.tool_output_token_limit,
        )
    )


def _stored_context_limits(
    *,
    adapter: ProviderAdapter,
    base_url: str,
    model: str,
) -> ModelContextLimits | None:
    """Recover saved limits for an exact endpoint/model identity.

    Desktop hot-switch RPCs historically send provider/model connection fields
    but not the optional context metadata. Looking up an exact saved connection
    here keeps cold start and hot switch behavior identical without guessing from
    model names. Conflicting saved limits fail conservative and fall back to the
    host/runtime defaults rather than picking one arbitrarily.
    """

    try:
        entries = ModelConfigStore().list_models()
    except Exception:
        return None

    target_base = _normalized_base_url(adapter, base_url)
    target_model = _text(model)
    matches: list[ModelContextLimits] = []
    for entry in entries:
        if entry.adapter is not adapter:
            continue
        if entry.model != target_model:
            continue
        if _normalized_base_url(entry.adapter, entry.base_url) != target_base:
            continue
        if _has_authoritative_context_limits(entry.context_limits):
            matches.append(entry.context_limits)

    if not matches:
        return None
    first = matches[0]
    if any(candidate != first for candidate in matches[1:]):
        return None
    return first


def validate_runtime_reasoning(
    *,
    model: str,
    provider: str,
    base_url: str,
    reasoning: ReasoningRequest | None,
) -> dict[str, object] | None:
    capability = reasoning_capability(
        model=model,
        adapter=provider,
        base_url=base_url,
    )
    if reasoning is None:
        return capability
    if capability is None:
        raise ValueError(f"Model {model!r} does not advertise a supported reasoning control")
    if reasoning.kind.value != str(capability.get("kind") or ""):
        raise ValueError(
            f"Reasoning kind {reasoning.kind.value!r} is not supported by model {model!r}"
        )
    supported = {
        str(option.get("value") or "")
        for option in capability.get("options") or []
        if isinstance(option, dict)
    }
    if reasoning.value not in supported:
        raise ValueError(
            f"Reasoning value {reasoning.value!r} is not supported by model {model!r}"
        )
    return capability


def build_runtime_model_platform(
    *,
    provider: str,
    base_url: str,
    model: str,
    api_key: str,
    vision: bool = True,
    request_timeout_seconds: float = 120.0,
    context_limits: ModelContextLimits | Mapping[str, object] | None = None,
):
    provider_text = _text(provider).casefold()
    if not provider_text:
        raise ValueError("provider is required")
    try:
        adapter = ProviderAdapter(provider_text)
    except ValueError as exc:
        raise ValueError(f"unsupported provider adapter: {provider_text}") from exc
    if adapter not in {
        ProviderAdapter.OPENAI,
        ProviderAdapter.OPENAI_COMPATIBLE,
        ProviderAdapter.OPENCODE_GO,
    }:
        raise ValueError(f"provider adapter is not executable: {adapter.value}")

    selected_model = _text(model)
    if not selected_model:
        raise ValueError("model is required")
    secret = _text(api_key)
    if not secret:
        raise ValueError("API key is required")

    resolved_base_url = _normalized_base_url(adapter, base_url)
    if adapter is ProviderAdapter.OPENAI_COMPATIBLE and not resolved_base_url:
        raise ValueError("OpenAI-compatible mode requires baseUrl")

    connection = ProviderConnection(
        provider_id=_PROVIDER_ID,
        adapter=adapter,
        credential_ref=CredentialRef.runtime(_RUNTIME_KEY_ALIAS),
        base_url=resolved_base_url,
        display_name="Loom Primary",
    )
    capabilities = set(AGENT_FAST_ROLE.required_capabilities)
    if bool(vision):
        capabilities.add(ModelCapability.VISION)

    env_limits = model_context_limits_from_env()
    saved_limits = _stored_context_limits(
        adapter=adapter,
        base_url=resolved_base_url,
        model=selected_model,
    )
    if isinstance(context_limits, ModelContextLimits):
        resolved_context_limits = context_limits
    elif isinstance(context_limits, Mapping):
        resolved_context_limits = model_context_limits_from_mapping(
            context_limits,
            fallback=saved_limits or env_limits,
        )
    elif saved_limits is not None:
        resolved_context_limits = saved_limits
    else:
        resolved_context_limits = env_limits

    binding = ModelBinding(
        role_id=AGENT_FAST_ROLE.role_id,
        provider_id=connection.provider_id,
        model=selected_model,
        capabilities=frozenset(capabilities),
        context_limits=resolved_context_limits,
    )
    configuration = AIConfiguration.build(
        roles=(AGENT_FAST_ROLE,),
        providers=(connection,),
        bindings=(binding,),
    )
    resolver = CredentialResolver(
        runtime_lookup=lambda alias: secret if alias == _RUNTIME_KEY_ALIAS else None
    )
    platform = build_ai_platform(
        configuration,
        credential_resolver=resolver,
        request_timeout_seconds=float(request_timeout_seconds),
    )
    wrapped = ComputerTransientInputPlatform(platform)
    # Keep the effective connection (not stale UI input) in RAM so isolated local
    # drivers such as UFO use exactly the same provider routing as Loom itself.
    # This private metadata never crosses Runtime status, diagnostics, or durable
    # tool-call boundaries.
    setattr(
        wrapped,
        "_loom_model_connection",
        {
            "provider": adapter.value,
            "base_url": resolved_base_url,
            "model": selected_model,
            "api_key": secret,
            "vision": bool(vision),
            "context_limits": resolved_context_limits.as_safe_dict(),
        },
    )
    return wrapped


__all__ = [
    "build_runtime_model_platform",
    "validate_runtime_reasoning",
]
