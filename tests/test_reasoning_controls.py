from __future__ import annotations

import threading
from types import SimpleNamespace

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
import app.app_server_reasoning as app_server_reasoning
from app.app_server_reasoning import ReasoningManagedLoomAppServerService
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


def test_deepseek_catalog_exposes_official_thinking_efforts() -> None:
    capability = reasoning_capability(
        model="deepseek-flash",
        adapter="openai-compatible",
        base_url="https://api.deepseek.com",
    )

    assert capability is not None
    assert capability["kind"] == "openai-effort"
    assert capability["defaultValue"] == "low"
    assert _option_values(capability) == ["none", "low", "high", "max"]


def test_gpt_5_6_sol_catalog_exposes_codex_advanced_efforts() -> None:
    capability = reasoning_capability(
        model="gpt-5.6-sol",
        adapter="openai",
    )

    assert capability is not None
    assert capability["kind"] == "openai-effort"
    assert capability["defaultValue"] == "low"
    assert _option_values(capability) == ["low", "medium", "high", "xhigh", "max", "ultra"]
    advanced = [option for option in capability["options"] if option["advanced"]]  # type: ignore[index]
    assert [option["value"] for option in advanced] == ["max", "ultra"]


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



def test_thread_model_switch_uses_canonical_server_connection(monkeypatch, tmp_path) -> None:
    service = object.__new__(ReasoningManagedLoomAppServerService)
    session = SimpleNamespace(
        session_id="thread-1",
        model_selection="builtin:minimax",
        model="MiniMax-M3",
        model_provider="openai-compatible",
        model_base_url="https://api.minimaxi.com/v1",
        model_vision=True,
        reasoning_kind="",
        reasoning_value="",
    )
    installed: dict[str, object] = {}
    saved: list[object] = []
    notifications: list[tuple[str, dict[str, object]]] = []

    service._load = lambda _session_id: session
    service._thread_model_blocked = lambda _session: False
    service._runtime_home = lambda: tmp_path
    service.runtime = SimpleNamespace(
        set_session_model=lambda session_id, platform, reasoning=None: installed.update(
            session_id=session_id,
            platform=platform,
            reasoning=reasoning,
        )
    )
    service.store = SimpleNamespace(save=lambda value: saved.append(value))
    service._record = lambda value, active=False: {
        "id": value.session_id,
        "modelSelection": value.model_selection,
        "model": value.model,
        "modelBaseUrl": value.model_base_url,
    }
    service._thread_runtime_patch = lambda _session, _capability=None: {"model": _session.model}
    service._notify = lambda method, params: notifications.append((method, params))

    monkeypatch.setattr(
        app_server_reasoning,
        "resolve_model_spec",
        lambda selection, model="", home=None: {
            "selection": "builtin:deepseek",
            "provider": "openai-compatible",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "apiKey": "deepseek-secret",
            "vision": False,
            "contextLimits": {
                "contextWindowTokens": 65_536,
                "autoCompactTokenLimit": 49_152,
            },
            "reasoning": {
                "kind": "openai-effort",
                "value": "high",
            },
        },
    )
    monkeypatch.setattr(
        app_server_reasoning,
        "validate_runtime_reasoning",
        lambda **_kwargs: None,
    )
    platform = object()
    captured: dict[str, object] = {}

    def build_platform(**kwargs):
        captured.update(kwargs)
        return platform

    monkeypatch.setattr(app_server_reasoning, "build_runtime_model_platform", build_platform)

    result = service.thread_set_model(
        {
            "threadId": "thread-1",
            "selection": "builtin:minimax",
            "provider": "openai-compatible",
            "baseUrl": "https://api.minimaxi.com/v1",
            "model": "deepseek-flash",
            "apiKey": "minimax-secret",
            "reasoningKind": "minimax-thinking",
            "reasoningValue": "adaptive",
        }
    )

    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["api_key"] == "deepseek-secret"
    assert captured["model"] == "deepseek-flash"
    assert captured["vision"] is False
    assert captured["context_limits"] == {
        "contextWindowTokens": 65_536,
        "autoCompactTokenLimit": 49_152,
    }
    assert session.model_selection == "builtin:deepseek"
    assert session.model_base_url == "https://api.deepseek.com"
    assert session.reasoning_kind == "openai-effort"
    assert installed["platform"] is platform
    assert result["thread"]["modelSelection"] == "builtin:deepseek"
    assert saved == [session]
    assert notifications[-1][0] == "thread/updated"


def test_thread_model_switch_keeps_sight_when_resolution_omits_vision(monkeypatch, tmp_path) -> None:
    """Silence from selection resolution is not a denial.

    Only OpenCode profiles carry a ``vision`` key, so reading the omission as
    ``False`` bound DeepSeek, MiniMax, managed and saved models to a platform
    without VISION -- which strips images out of the request on the way to the
    provider, leaving the agent to report that nothing was attached.
    """

    service = object.__new__(ReasoningManagedLoomAppServerService)
    session = SimpleNamespace(
        session_id="thread-1",
        model_selection="builtin:deepseek",
        model="deepseek-flash",
        model_provider="openai-compatible",
        model_base_url="https://api.deepseek.com",
        model_vision=True,
        reasoning_kind="",
        reasoning_value="",
    )
    service._load = lambda _session_id: session
    service._thread_model_blocked = lambda _session: False
    service._runtime_home = lambda: tmp_path
    service.runtime = SimpleNamespace(set_session_model=lambda *_args, **_kwargs: None)
    service.store = SimpleNamespace(save=lambda _value: None)
    service._record = lambda value, active=False: {"id": value.session_id}
    service._thread_runtime_patch = lambda _session, _capability=None: {}
    service._notify = lambda _method, _params: None

    monkeypatch.setattr(
        app_server_reasoning,
        "resolve_model_spec",
        lambda selection, model="", home=None: {
            "selection": "builtin:deepseek",
            "provider": "openai-compatible",
            "baseUrl": "https://api.deepseek.com",
            "model": "deepseek-v4-pro",
            "apiKey": "deepseek-secret",
        },
    )
    monkeypatch.setattr(
        app_server_reasoning,
        "validate_runtime_reasoning",
        lambda **_kwargs: None,
    )
    captured: dict[str, object] = {}

    def build_platform(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(app_server_reasoning, "build_runtime_model_platform", build_platform)

    service.thread_set_model(
        {
            "threadId": "thread-1",
            "selection": "builtin:deepseek",
            "model": "deepseek-v4-pro",
        }
    )

    assert captured["vision"] is True
    assert session.model_vision is True


def test_thread_runtime_rebuild_heals_a_stale_vision_record(monkeypatch, tmp_path) -> None:
    """Rebuilding the platform must not leave the old capability on the record.

    The thread's own value is what the composer consults, and it blocks the
    attachment before a turn can start -- so a thread that recorded
    ``vision: False`` under an earlier catalogue could never reach the turn that
    would have refreshed it.
    """

    service = object.__new__(ReasoningManagedLoomAppServerService)
    session = SimpleNamespace(
        session_id="thread-1",
        model_selection="builtin:opencode-go:grok-4.6",
        model="grok-4.6",
        model_provider="opencode-go",
        model_base_url="https://opencode.ai/zen/go/v1",
        model_vision=False,
        reasoning_kind="",
        reasoning_value="",
    )
    saved: list[object] = []
    service._runtime_home = lambda: tmp_path
    service._session_reasoning = lambda _session: None
    service._thread_uses_default_model = lambda _session: False
    service.runtime = SimpleNamespace(
        has_session_model=lambda _session_id: False,
        set_session_model=lambda *_args, **_kwargs: None,
    )
    service.store = SimpleNamespace(save=lambda value: saved.append(value))

    monkeypatch.setattr(
        app_server_reasoning,
        "resolve_model_spec",
        lambda selection, model="", home=None: {
            "provider": "opencode-go",
            "baseUrl": "https://opencode.ai/zen/go/v1",
            "model": "grok-4.6",
            "apiKey": "opencode-secret",
            "vision": True,
        },
    )
    monkeypatch.setattr(
        app_server_reasoning,
        "validate_runtime_reasoning",
        lambda **_kwargs: None,
    )
    captured: dict[str, object] = {}

    def build_platform(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(app_server_reasoning, "build_runtime_model_platform", build_platform)

    service._ensure_thread_model_runtime(session)

    assert captured["vision"] is True
    assert session.model_vision is True
    assert saved == [session]


def test_hot_model_switch_returns_lightweight_runtime_patch(monkeypatch) -> None:
    service = object.__new__(ReasoningManagedLoomAppServerService)
    service._guard = threading.RLock()
    service.model = "old-model"
    service.vision = True
    service.runtime = SimpleNamespace(
        platform=object(),
        reasoning=None,
        reasoning_capability=None,
        supports_vision=True,
    )
    service.settings_store = SimpleNamespace(
        snapshot=lambda: {"capabilities": {"attachments": True}}
    )
    service._model_change_blockers = lambda: []
    notifications: list[tuple[str, dict[str, object]]] = []
    service._notify = lambda method, params: notifications.append((method, params))

    platform = object()
    monkeypatch.setattr(
        app_server_reasoning,
        "build_runtime_model_platform",
        lambda **_kwargs: platform,
    )
    monkeypatch.setattr(
        app_server_reasoning,
        "validate_runtime_reasoning",
        lambda **_kwargs: None,
    )

    result = service.runtime_set_model(
        {
            "provider": "openai-compatible",
            "baseUrl": "https://example.invalid/v1",
            "model": "new-model",
            "apiKey": "secret",
            "vision": False,
        }
    )

    assert service.runtime.platform is platform
    assert service.model == "new-model"
    assert result["model"] == "new-model"
    assert result["attachments"]["images"] is False
    assert "capabilityStatus" not in result
    assert notifications[-1][0] == "runtime/updated"
