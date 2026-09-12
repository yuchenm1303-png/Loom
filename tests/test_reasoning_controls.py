from __future__ import annotations

import pytest

from app.ai import (
    AIMessage,
    ChatRequest,
    CredentialRef,
    MessageRole,
    ModelCapability,
    ModelProfile,
    OpenAIChatBackend,
    ProviderAdapter,
    ProviderConnection,
    ReasoningKind,
    ReasoningRequest,
)
from app.ai.model_store import ModelConfigStore
from app.ai.reasoning_catalog import reasoning_capability, resolved_reasoning
from app.ai.reasoning_store import ReasoningConfigStore
from loom_app_server import _validate_reasoning_for_runtime
from loom_model_bridge import _describe_model, _set_reasoning


def _option_values(capability: dict[str, object]) -> list[str]:
    return [str(option["value"]) for option in capability["options"]]  # type: ignore[index]


def _fake_backend(model: str = "test-model") -> OpenAIChatBackend:
    connection = ProviderConnection(
        provider_id="test-provider",
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        credential_ref=CredentialRef.runtime("test-key"),
        base_url="https://example.invalid/v1",
        display_name="Test Provider",
    )
    profile = ModelProfile(
        profile_id="test-profile",
        provider=connection.provider_id,
        model=model,
        capabilities=frozenset({ModelCapability.TEXT}),
    )
    return OpenAIChatBackend(
        connection=connection,
        profile=profile,
        api_key="test-secret",
        client=object(),
    )


def _request(reasoning: ReasoningRequest) -> ChatRequest:
    return ChatRequest(
        messages=(AIMessage(role=MessageRole.USER, content="hello"),),
        reasoning=reasoning,
    )


def test_minimax_m3_hosted_catalog_uses_safe_thinking_modes() -> None:
    capability = reasoning_capability(
        model="MiniMax-M3",
        adapter="openai-compatible",
        base_url="https://api.minimaxi.com/v1",
    )

    assert capability is not None
    assert capability["kind"] == "minimax-thinking"
    assert capability["defaultValue"] == "adaptive"
    assert _option_values(capability) == ["disabled", "adaptive"]


def test_gpt_5_6_sol_catalog_exposes_only_public_api_efforts() -> None:
    capability = reasoning_capability(
        model="gpt-5.6-sol",
        adapter="openai",
    )

    assert capability is not None
    assert capability["kind"] == "openai-effort"
    assert capability["defaultValue"] == "low"
    assert _option_values(capability) == ["none", "low", "medium", "high", "xhigh", "max"]
    advanced = [option for option in capability["options"] if option["advanced"]]  # type: ignore[index]
    assert [option["value"] for option in advanced] == ["max"]


def test_gpt_6_astra_does_not_advertise_none_or_ultra() -> None:
    capability = reasoning_capability(model="gpt-6-astra", adapter="openai")

    assert capability is not None
    assert capability["defaultValue"] == "low"
    assert _option_values(capability) == ["low", "medium", "high", "xhigh", "max"]


def test_gpt_5_4_keeps_codex_product_default_with_api_accurate_options() -> None:
    capability = reasoning_capability(model="gpt-5.4", adapter="openai")

    assert capability is not None
    assert capability["defaultValue"] == "medium"
    assert _option_values(capability) == ["none", "low", "medium", "high", "xhigh"]


def test_legacy_gpt_5_does_not_invent_xhigh() -> None:
    capability = reasoning_capability(model="gpt-5", adapter="openai")

    assert capability is not None
    assert _option_values(capability) == ["minimal", "low", "medium", "high"]


def test_unknown_model_does_not_invent_reasoning_control() -> None:
    assert reasoning_capability(
        model="qwen-plus",
        adapter="openai-compatible",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ) is None


def test_incompatible_saved_reasoning_falls_back_to_model_default() -> None:
    capability, selected = resolved_reasoning(
        model="MiniMax-M3",
        adapter="openai-compatible",
        base_url="https://api.minimaxi.com/v1",
        saved=ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "high"),
    )

    assert capability is not None
    assert selected == ReasoningRequest(ReasoningKind.MINIMAX_THINKING, "adaptive")


def test_stale_ultra_saved_for_openai_falls_back_to_model_default() -> None:
    capability, selected = resolved_reasoning(
        model="gpt-5.6-sol",
        adapter="openai",
        base_url="",
        saved=ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "ultra"),
    )

    assert capability is not None
    assert selected == ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "low")


def test_minimax_reasoning_is_sent_as_thinking_object() -> None:
    kwargs = _fake_backend("MiniMax-M3")._request_kwargs(
        _request(ReasoningRequest(ReasoningKind.MINIMAX_THINKING, "adaptive"))
    )

    assert kwargs["extra_body"] == {"thinking": {"type": "adaptive"}}


def test_openai_reasoning_is_sent_as_reasoning_effort() -> None:
    kwargs = _fake_backend("gpt-5.6-sol")._request_kwargs(
        _request(ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "high"))
    )

    assert kwargs["extra_body"] == {"reasoning_effort": "high"}


def test_stale_openai_ultra_is_normalized_to_strongest_supported_wire_effort() -> None:
    kwargs = _fake_backend("gpt-5.6-sol")._request_kwargs(
        _request(ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "ultra"))
    )

    assert kwargs["extra_body"] == {"reasoning_effort": "max"}


def test_persistent_alias_uses_codex_wire_value() -> None:
    kwargs = _fake_backend("gpt-5.6-sol")._request_kwargs(
        _request(ReasoningRequest(ReasoningKind.OPENAI_EFFORT, "persistent"))
    )

    assert kwargs["extra_body"] == {"reasoning_effort": "disabled"}


def test_app_server_accepts_only_catalogued_minimax_hosted_modes() -> None:
    capability = _validate_reasoning_for_runtime(
        model="MiniMax-M3",
        provider="openai-compatible",
        base_url="https://api.minimaxi.com/v1",
        reasoning=ReasoningRequest(ReasoningKind.MINIMAX_THINKING, "adaptive"),
    )
    assert capability is not None

    with pytest.raises(SystemExit, match="not supported"):
        _validate_reasoning_for_runtime(
            model="MiniMax-M3",
            provider="openai-compatible",
            base_url="https://api.minimaxi.com/v1",
            reasoning=ReasoningRequest(ReasoningKind.MINIMAX_THINKING, "enabled"),
        )


def test_reasoning_preferences_are_scoped_to_connection_and_model(tmp_path) -> None:
    secrets: dict[str, str] = {}
    model_store = ModelConfigStore(
        tmp_path,
        secret_getter=secrets.get,
        secret_setter=lambda alias, value: secrets.__setitem__(alias, value),
    )
    reasoning_store = ReasoningConfigStore(tmp_path)
    saved = model_store.save_model(
        display_name="OpenAI Test",
        adapter="openai",
        base_url="",
        model="gpt-5.6-sol",
        api_key="sk-test",
    )

    first = _set_reasoning(
        model_store,
        reasoning_store,
        {
            "selection": saved.selection,
            "model": "gpt-5.6-sol",
            "kind": "openai-effort",
            "value": "high",
        },
    )
    second_before = _describe_model(
        model_store,
        reasoning_store,
        saved.selection,
        "gpt-5.4",
    )
    second = _set_reasoning(
        model_store,
        reasoning_store,
        {
            "selection": saved.selection,
            "model": "gpt-5.4",
            "kind": "openai-effort",
            "value": "low",
        },
    )
    first_again = _describe_model(
        model_store,
        reasoning_store,
        saved.selection,
        "gpt-5.6-sol",
    )

    assert first["reasoning"]["value"] == "high"
    assert second_before["reasoning"]["value"] == "medium"
    assert second["reasoning"]["value"] == "low"
    assert first_again["reasoning"]["value"] == "high"
