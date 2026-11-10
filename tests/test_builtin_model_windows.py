from __future__ import annotations

import loom_model_bridge as bridge


def test_the_built_in_minimax_models_declare_the_window_their_docs_publish():
    # M3.1-Flash had none: compaction never fired and a long-goal line sent 606k tokens per request.
    for model in ("MiniMax-M3.1-Flash", "MiniMax-M3.1-Flash-Preview"):
        profile = bridge._safe_minimax(model, {})
        assert profile["contextLimits"] == {"contextWindowTokens": 1_000_000}, model
        assert "platform.minimax.io" in profile["contextLimitsSource"]
    assert bridge._safe_minimax("MiniMax-M3", {})["contextLimits"] == {"contextWindowTokens": 512_000}


def test_a_window_is_not_declared_for_another_endpoint_that_reuses_the_name():
    profile = bridge._safe_minimax("MiniMax-M3.1-Flash", {"LOOM_MINIMAX_BASE_URL": "https://relay.example.com/v1"})
    assert "contextLimits" not in profile
