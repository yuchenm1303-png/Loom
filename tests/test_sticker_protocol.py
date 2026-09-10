from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from app.agent_runtime.stickers import (
    CHAT_STICKER_CATALOG,
    INLINE_STICKER_STRUCTURED_PLAN_BEGIN,
    INLINE_STICKER_STRUCTURED_PLAN_END,
    StickerContext,
    StickerPreferences,
    StickerStreamSanitizer,
    build_sticker_system_prompt,
    extract_keys,
    finalize_reply,
)
from app.agent_runtime.streaming_runtime import (
    StreamingAgentRuntime,
    _strip_incomplete_sticker_control_fragments,
)


def test_catalog_keeps_all_current_ai_ledger_assets() -> None:
    assert len(CHAT_STICKER_CATALOG) == 19
    assert set(CHAT_STICKER_CATALOG) == {
        "joy_burst",
        "affection_hug",
        "health_check",
        "thinking_soft",
        "cheer_power",
        "pout_no",
        "comfort_friend",
        "red_packet_congrats",
        "gift_for_you",
        "sparkle_excited",
        "soft_smile",
        "got_it_point",
        "heart_thanks",
        "confident_ready",
        "playful_wink",
        "confused_study",
        "confirm_yes",
        "idea_drawing",
        "reject_no",
    }


def test_preferences_match_current_worker_defaults_and_limits() -> None:
    assert StickerPreferences.normalize().to_dict() == {
        "schema": "ai_ledger_chat_expression_preferences_v2",
        "frequency": 50,
        "intensity": 50,
        "maxPerReply": 0,
        "repeatCount": 1,
    }
    clamped = StickerPreferences.normalize(
        {"frequency": 200, "intensity": -4, "maxPerReply": 200, "repeatCount": 9}
    )
    assert (clamped.frequency, clamped.intensity, clamped.max_per_reply, clamped.repeat_count) == (100, 0, 64, 4)


def test_runtime_sticker_preferences_persist_and_reload(tmp_path) -> None:
    runtime = object.__new__(StreamingAgentRuntime)
    runtime._sticker_guard = threading.RLock()
    runtime._sticker_preferences = StickerPreferences()
    runtime.store = SimpleNamespace(root=tmp_path / "agent_runtime" / "sessions")

    saved = runtime.set_sticker_preferences(
        {"frequency": 72, "intensity": 88, "maxPerReply": 5, "repeatCount": 2}
    )

    assert saved == {
        "schema": "ai_ledger_chat_expression_preferences_v2",
        "frequency": 72,
        "intensity": 88,
        "maxPerReply": 5,
        "repeatCount": 2,
    }
    target = tmp_path / "agent_runtime" / "sticker-preferences.json"
    assert json.loads(target.read_text(encoding="utf-8")) == saved
    assert runtime._load_sticker_preferences().to_dict() == saved


def test_stream_prompt_uses_existing_candidate_marker_protocol() -> None:
    prompt = build_sticker_system_prompt(
        StickerPreferences(),
        StickerContext(user_text="继续", streaming=True),
    )
    assert "AI_LEDGER_INLINE_STICKER" in prompt
    assert "joy_burst" in prompt
    assert "候选" in prompt


def test_stream_sanitizer_buffers_marker_across_provider_chunks() -> None:
    sanitizer = StickerStreamSanitizer(
        StickerPreferences(frequency=100, intensity=0, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="给我一个详细方案", streaming=True),
    )
    visible = ""
    visible += sanitizer.push("第一部分已经完成。[[AI_LEDGER_INLI")
    visible += sanitizer.push("NE_STICKER:joy_burst]] 接下来继续处理第二部分。")
    visible += sanitizer.finish()
    assert "[[AI_LEDGER_INLI" not in visible
    assert len(extract_keys(sanitizer.value())) <= 4


def test_stream_sanitizer_drops_truncated_marker_at_finish() -> None:
    sanitizer = StickerStreamSanitizer(
        StickerPreferences(frequency=100, intensity=0, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="继续", streaming=True),
    )
    visible = sanitizer.push("已经完成。[[AI_LEDGER_INLINE_STICKER:soft_smile]")
    visible += sanitizer.finish()
    assert "[[AI_LEDGER_INLINE_STICKER" not in visible
    assert "[[AI_LEDGER_INLINE_STICKER" not in sanitizer.value()


def test_nonstream_truncated_control_marker_is_removed_before_persisting() -> None:
    assert _strip_incomplete_sticker_control_fragments(
        "已经完成。[[AI_LEDGER_INLINE_STICKER:soft_smile]"
    ) == "已经完成。"
    assert _strip_incomplete_sticker_control_fragments(
        "已经完成。[[AI_LEDGER_INLINE_STICKER:soft_smile"
    ) == "已经完成。"
    assert _strip_incomplete_sticker_control_fragments(
        "已经完成。[[AI_LEDGER_INLI"
    ) == "已经完成。"
    complete = "已经完成。[[AI_LEDGER_INLINE_STICKER:soft_smile]]"
    assert _strip_incomplete_sticker_control_fragments(complete) == complete


def test_nonstream_sidecar_is_removed_before_user_visible_text() -> None:
    body = (
        "第一步已经完成，可以继续下一步。\n\n"
        "第二步也已经处理完成，现在可以开始验证。\n"
        f"{INLINE_STICKER_STRUCTURED_PLAN_BEGIN}"
        '{"schema":"ai_ledger_inline_sticker_plan_v1","candidates":['
        '{"anchor":"第一步已经完成，可以继续下一步。","assetKey":"confirm_yes","score":0.95},'
        '{"anchor":"第二步也已经处理完成，现在可以开始验证。","assetKey":"confident_ready","score":0.90}'
        "]}"
        f"{INLINE_STICKER_STRUCTURED_PLAN_END}"
    )
    result = finalize_reply(
        body,
        StickerPreferences(frequency=100, intensity=0, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="继续", streaming=False),
    )
    assert INLINE_STICKER_STRUCTURED_PLAN_BEGIN not in result.text
    assert INLINE_STICKER_STRUCTURED_PLAN_END not in result.text
    assert result.diagnostics["structuredPlanFound"] is True
    assert len(result.keys) >= 1


def test_repeat_and_max_are_mechanical_hard_limits() -> None:
    result = finalize_reply(
        "这一步已经完成。[[AI_LEDGER_INLINE_STICKER:joy_burst]] 继续下一步即可。[[AI_LEDGER_INLINE_STICKER:confirm_yes]]",
        StickerPreferences(frequency=100, intensity=0, max_per_reply=3, repeat_count=2),
        StickerContext(user_text="继续", streaming=True),
    )
    assert len(result.keys) <= 3


def test_frequency_zero_disables_ordinary_stickers() -> None:
    result = finalize_reply(
        "完成了。[[AI_LEDGER_INLINE_STICKER:joy_burst]]",
        StickerPreferences(frequency=0, intensity=50, max_per_reply=0, repeat_count=1),
        StickerContext(user_text="继续", streaming=True),
    )
    assert extract_keys(result.text) == []


def test_current_turn_explicit_opt_out_removes_model_marker() -> None:
    result = finalize_reply(
        "可以。[[AI_LEDGER_INLINE_STICKER:soft_smile]]",
        StickerPreferences(frequency=100, intensity=100, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="这次不要发表情包", streaming=True),
    )
    assert extract_keys(result.text) == []
    assert result.diagnostics["scene"]["reason"] == "user_explicit_opt_out"


def test_response_path_disallow_is_a_hard_boundary() -> None:
    result = finalize_reply(
        "完成了。[[AI_LEDGER_INLINE_STICKER:joy_burst]]",
        StickerPreferences(frequency=100, intensity=100, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="继续", streaming=True, allow_stickers=False),
    )
    assert extract_keys(result.text) == []
    assert result.diagnostics["scene"]["reason"] == "response_path_disallows_stickers"
