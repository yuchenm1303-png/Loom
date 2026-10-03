from types import SimpleNamespace

import pytest

import app.app_server_reasoning as app_server_reasoning
from app.agent_runtime import AgentStatus
from app.app_server_reasoning import ReasoningManagedLoomAppServerService


def _ant_ling_spec() -> dict[str, object]:
    return {
        "selection": "builtin:ant-ling:Ling-2.6-flash",
        "provider": "openai-compatible",
        "baseUrl": "https://account.smirel.com/model/v1",
        "model": "Ling-2.6-flash",
        "apiKey": "",
        "authMode": "loom-account",
        "vision": False,
        "reasoning": None,
    }


def test_thread_model_switch_uses_scoped_loom_credential_for_ant_ling(monkeypatch, tmp_path) -> None:
    service = object.__new__(ReasoningManagedLoomAppServerService)
    session = SimpleNamespace(
        session_id="thread-ant-ling",
        status=AgentStatus.COMPLETED,
        model_selection="builtin:minimax",
        model="MiniMax-M3",
        model_provider="openai-compatible",
        model_base_url="https://api.minimaxi.com/v1",
        model_vision=True,
        reasoning_kind="",
        reasoning_value="",
    )
    installed: dict[str, object] = {}

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
    service.store = SimpleNamespace(save=lambda _value: None)
    service._record = lambda value, active=False: {
        "id": value.session_id,
        "modelSelection": value.model_selection,
        "model": value.model,
    }
    service._thread_runtime_patch = lambda _session, _capability=None: {"model": _session.model}
    service._notify = lambda _method, _params: None

    monkeypatch.setattr(
        app_server_reasoning,
        "resolve_model_spec",
        lambda selection, model="", home=None: _ant_ling_spec(),
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

    result = service.thread_set_model(
        {
            "threadId": "thread-ant-ling",
            "selection": "builtin:ant-ling:Ling-2.6-flash",
            "provider": "not-trusted",
            "baseUrl": "https://evil.invalid/v1",
            "model": "Ling-2.6-flash",
            "apiKey": "loom_model_test-scoped-token",
        }
    )

    assert captured["base_url"] == "https://account.smirel.com/model/v1"
    assert captured["api_key"] == "loom_model_test-scoped-token"
    assert captured["model"] == "Ling-2.6-flash"
    assert captured["vision"] is False
    assert session.model_selection == "builtin:ant-ling:Ling-2.6-flash"
    assert session.model_base_url == "https://account.smirel.com/model/v1"
    assert result["thread"]["modelSelection"] == "builtin:ant-ling:Ling-2.6-flash"


def test_thread_model_switch_rejects_non_loom_credential_for_ant_ling(monkeypatch, tmp_path) -> None:
    service = object.__new__(ReasoningManagedLoomAppServerService)
    session = SimpleNamespace(session_id="thread-ant-ling", status=AgentStatus.COMPLETED)
    service._load = lambda _session_id: session
    service._thread_model_blocked = lambda _session: False
    service._runtime_home = lambda: tmp_path

    monkeypatch.setattr(
        app_server_reasoning,
        "resolve_model_spec",
        lambda selection, model="", home=None: _ant_ling_spec(),
    )

    with pytest.raises(ValueError, match="credential is invalid"):
        service.thread_set_model(
            {
                "threadId": "thread-ant-ling",
                "selection": "builtin:ant-ling:Ling-2.6-flash",
                "model": "Ling-2.6-flash",
                "apiKey": "provider-upstream-key-must-not-be-accepted",
            }
        )


def test_persisted_ant_ling_thread_can_reuse_process_account_credential(monkeypatch) -> None:
    service = object.__new__(ReasoningManagedLoomAppServerService)
    monkeypatch.setenv("LOOM_ACCOUNT_MODEL_CREDENTIAL", "loom_model_test-from-process")
    assert service._model_api_key(_ant_ling_spec()) == "loom_model_test-from-process"
