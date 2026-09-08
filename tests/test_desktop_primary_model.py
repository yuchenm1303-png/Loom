from __future__ import annotations

import pytest

from loom_desktop import (
    _MINIMAX_BASE_URL,
    _MINIMAX_DEFAULT_MODEL,
    _default_primary_model,
    _primary_minimax_key,
)


def test_minimax_is_the_default_desktop_primary_model():
    provider, base_url, model, secret = _default_primary_model(
        None,
        {"MINIMAX_API_KEY": "test-minimax-secret"},
    )

    assert provider == "openai-compatible"
    assert base_url == _MINIMAX_BASE_URL == "https://api.minimaxi.com/v1"
    assert model == _MINIMAX_DEFAULT_MODEL == "MiniMax-M3"
    assert secret == "test-minimax-secret"


def test_raw_model_switch_stays_on_minimax_primary_connection():
    provider, base_url, model, secret = _default_primary_model(
        "MiniMax-M2.7-highspeed",
        {"LOOM_PRIMARY_API_KEY": "primary-secret"},
    )

    assert provider == "openai-compatible"
    assert base_url == _MINIMAX_BASE_URL
    assert model == "MiniMax-M2.7-highspeed"
    assert secret == "primary-secret"


def test_dashscope_key_is_not_a_primary_agent_credential():
    env = {"DASHSCOPE_API_KEY": "computer-use-only"}

    assert _primary_minimax_key(env) == ""
    with pytest.raises(RuntimeError, match="MiniMax primary API key is not configured"):
        _default_primary_model(None, env)


def test_primary_key_precedence_is_explicit_and_does_not_touch_dashscope():
    env = {
        "MINIMAX_API_KEY": "minimax-secret",
        "LOOM_PRIMARY_API_KEY": "primary-secret",
        "LOOM_API_KEY": "generic-secret",
        "DASHSCOPE_API_KEY": "computer-secret",
    }

    assert _primary_minimax_key(env) == "minimax-secret"
