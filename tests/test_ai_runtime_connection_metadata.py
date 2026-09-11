from __future__ import annotations

from app.ai import (
    AGENT_FAST_ROLE,
    AIConfiguration,
    CredentialRef,
    CredentialResolver,
    ModelBinding,
    ModelCapability,
    ProviderAdapter,
    ProviderConnection,
    build_ai_platform,
)


def test_single_runtime_binding_exposes_ram_only_connection_for_local_drivers():
    connection = ProviderConnection(
        provider_id="test-provider",
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        credential_ref=CredentialRef.runtime("test-key"),
        base_url="https://example.test/v1",
    )
    capabilities = set(AGENT_FAST_ROLE.required_capabilities)
    capabilities.add(ModelCapability.VISION)
    configuration = AIConfiguration.build(
        roles=(AGENT_FAST_ROLE,),
        providers=(connection,),
        bindings=(
            ModelBinding(
                role_id=AGENT_FAST_ROLE.role_id,
                provider_id=connection.provider_id,
                model="vision-model",
                capabilities=frozenset(capabilities),
            ),
        ),
    )
    resolver = CredentialResolver(
        runtime_lookup=lambda alias: "runtime-secret" if alias == "test-key" else None
    )

    platform = build_ai_platform(
        configuration,
        credential_resolver=resolver,
        client_factory=lambda _connection, _profile, _secret: object(),
    )

    metadata = getattr(platform, "_loom_model_connection")
    assert metadata == {
        "profile_id": "agent.fast",
        "provider": "openai_compatible",
        "base_url": "https://example.test/v1",
        "model": "vision-model",
        "api_key": "runtime-secret",
        "vision": True,
    }
    assert "runtime-secret" not in repr(configuration)


def test_hot_switched_openai_metadata_uses_effective_provider_base(monkeypatch):
    import app.runtime_model_switch as model_switch

    class FakePlatform:
        pass

    monkeypatch.setattr(
        model_switch,
        "build_ai_platform",
        lambda *_args, **_kwargs: FakePlatform(),
    )

    platform = model_switch.build_runtime_model_platform(
        provider="openai",
        base_url="https://stale-compatible-endpoint.example/v1",
        model="vision-model",
        api_key="runtime-secret",
        vision=True,
    )
    metadata = getattr(platform, "_loom_model_connection")

    # The OpenAI adapter ignores custom baseUrl, so the local Computer Driver
    # must not resurrect a stale settings value and route UFO somewhere else.
    assert metadata["provider"] == "openai"
    assert metadata["base_url"] == ""
    assert metadata["model"] == "vision-model"
    assert metadata["api_key"] == "runtime-secret"
