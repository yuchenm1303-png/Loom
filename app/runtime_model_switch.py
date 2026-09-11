from __future__ import annotations

from typing import Any

from app.ai import (
    AGENT_FAST_ROLE,
    AIConfiguration,
    CredentialRef,
    CredentialResolver,
    ModelBinding,
    ModelCapability,
    ProviderAdapter,
    ProviderConnection,
    ReasoningRequest,
    build_ai_platform,
)
from app.ai.reasoning_catalog import reasoning_capability
from app.agent_runtime.computer_transient import ComputerTransientInputPlatform

_RUNTIME_KEY_ALIAS = "loom-api-key"
_PROVIDER_ID = "loom-primary"


def _text(value: Any) -> str:
    return str(value or "").strip()


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
):
    provider_text = _text(provider).casefold()
    if not provider_text:
        raise ValueError("provider is required")
    try:
        adapter = ProviderAdapter(provider_text)
    except ValueError as exc:
        raise ValueError(f"unsupported provider adapter: {provider_text}") from exc
    if adapter not in {ProviderAdapter.OPENAI, ProviderAdapter.OPENAI_COMPATIBLE}:
        raise ValueError(f"provider adapter is not executable: {adapter.value}")

    selected_model = _text(model)
    if not selected_model:
        raise ValueError("model is required")
    secret = _text(api_key)
    if not secret:
        raise ValueError("API key is required")

    resolved_base_url = "" if adapter is ProviderAdapter.OPENAI else _text(base_url)
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
    binding = ModelBinding(
        role_id=AGENT_FAST_ROLE.role_id,
        provider_id=connection.provider_id,
        model=selected_model,
        capabilities=frozenset(capabilities),
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
    return ComputerTransientInputPlatform(platform)


__all__ = [
    "build_runtime_model_platform",
    "validate_runtime_reasoning",
]
