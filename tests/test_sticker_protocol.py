from __future__ import annotations

import json
import re
import threading
from types import SimpleNamespace

from app.agent_runtime.stickers import (
    CHAT_STICKER_CATALOG,
    INLINE_STICKER_STRUCTURED_PLAN_BEGIN,
    INLINE_STICKER_STRUCTURED_PLAN_END,
    INLINE_STICKER_VISIBLE_MARKER_RE,
    StickerContext,
    StickerPreferences,
    StickerStreamSanitizer,
    analyze_scene,
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
    assert "前部、中部和后部" in prompt


def test_stream_sanitizer_buffers_marker_across_provider_chunks() -> None:
    sanitizer = StickerStreamSanitizer(
        StickerPreferences(frequency=100, intensity=0, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="给我一个详细方案", streaming=True),
    )
    visible = ""
    visible += sanitizer.push("第一部分已经完成。[[AI_LEDGER_INLI")
    visible += sanitizer.push("NE_STICKER:joy_burst]] 接下来继续处理第二部分。")
    visible += sanitizer.finish()

    # A well-formed marker is the protocol, not a leak: the client renders it as
    # a sticker. What must never survive is a *torn* one. Strip the whole
    # markers first, then assert no fragment is left -- asserting on the raw
    # prefix instead would also match every legitimate marker, so it could
    # never pass once a sticker was emitted at all.
    residue = INLINE_STICKER_VISIBLE_MARKER_RE.sub("", visible)
    assert "AI_LEDGER" not in residue
    assert "[[" not in residue
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


def test_stream_sanitizer_does_not_front_load_close_candidates() -> None:
    sanitizer = StickerStreamSanitizer(
        StickerPreferences(frequency=50, intensity=50, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="请给我详细分析", streaming=True),
    )
    first = "第一部分先说明当前状态和最重要的判断依据，这里已经形成一个完整结论。"
    close = "第二部分紧接着补充一个很短的说明。"
    later = "后面继续展开更多细节和验证步骤，" * 6 + "现在来到中后段，可以给出新的完整结论。"
    visible = sanitizer.push(first + "[[AI_LEDGER_INLINE_STICKER:soft_smile]]")
    visible += sanitizer.push(close + "[[AI_LEDGER_INLINE_STICKER:confirm_yes]]")
    visible += sanitizer.push(later + "[[AI_LEDGER_INLINE_STICKER:idea_drawing]] 继续收尾。")
    visible += sanitizer.finish()
    keys = extract_keys(sanitizer.value())
    assert len(keys) >= 2
    assert len(keys) < 3
    assert "[[AI_LEDGER_INLINE_STICKER" not in re.sub(
        r"\[\[AI_LEDGER_INLINE_STICKER:[a-z0-9_]+\]\]", "", visible, flags=re.I
    )


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


def test_damaged_provider_sticker_markers_never_persist_as_text() -> None:
    assert _strip_incomplete_sticker_control_fragments(
        "继续执行。[IA_LEDGER_INLINE_STICKER:got_it_point]"
    ) == "继续执行。"
    assert _strip_incomplete_sticker_control_fragments(
        "完成 AI_LEDGER_INLINE_STICKER:idea_drawing]] 收尾"
    ) == "完成  收尾"


def test_stream_sanitizer_repairs_damaged_marker_across_chunks() -> None:
    sanitizer = StickerStreamSanitizer(
        StickerPreferences(frequency=100, intensity=0, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="继续", streaming=True),
    )
    visible = sanitizer.push("继续执行。[IA_LEDGER_INLINE_STI")
    visible += sanitizer.push("CKER:got_it_point] 后续步骤。")
    visible += sanitizer.finish()

    residue = INLINE_STICKER_VISIBLE_MARKER_RE.sub("", visible)
    assert "LEDGER_INLINE_STICKER" not in residue
    assert sanitizer.diagnostics()["candidateCount"] == 1


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


def test_long_reply_supplements_and_spreads_front_loaded_candidates() -> None:
    lines = [
        "第一部分先确认当前现象并说明最主要的判断依据，这里可以形成一个完整结论。",
        "第二部分继续补充前置检查结果，并说明为什么这个方向值得优先处理。",
        "第三部分把目前已经确认的信息整理清楚，避免后面的判断受到干扰。",
        "第四部分开始进入中段分析，逐项说明实际影响和可能出现的边界情况。",
        "第五部分继续检查中间环节，并给出一个相对稳妥的处理建议供后续执行。",
        "第六部分验证前面的判断是否成立，同时排除一个常见但不相关的可能原因。",
        "第七部分进入后半段，说明剩余风险以及后续执行时需要注意的关键条件。",
        "第八部分继续给出后续步骤，让整个方案从诊断自然过渡到实际处理阶段。",
        "第九部分检查处理后的预期结果，并说明如何判断这次调整是否已经真正生效。",
        "第十部分补充一个容易忽略的细节，避免以后相同问题再次集中出现在前半段。",
        "第十一部分接近收尾，总结最值得保留的设置以及不建议继续改动的部分。",
        "第十二部分给出最终结论和下一步建议，整个回答到这里已经完整闭环。",
    ]
    reply = "\n".join(lines)
    body = (
        reply
        + "\n"
        + INLINE_STICKER_STRUCTURED_PLAN_BEGIN
        + json.dumps(
            {
                "schema": "ai_ledger_inline_sticker_plan_v1",
                "candidates": [
                    {"anchor": lines[0], "assetKey": "soft_smile", "score": 0.99},
                    {"anchor": lines[1], "assetKey": "confirm_yes", "score": 0.98},
                    {"anchor": lines[2], "assetKey": "idea_drawing", "score": 0.97},
                ],
            },
            ensure_ascii=False,
        )
        + INLINE_STICKER_STRUCTURED_PLAN_END
    )
    result = finalize_reply(
        body,
        StickerPreferences(frequency=50, intensity=50, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="继续", streaming=False),
    )
    positions = [
        match.start()
        for match in re.finditer(r"\[\[AI_LEDGER_INLINE_STICKER:[a-z0-9_]+\]\]", result.text, re.I)
    ]
    assert result.diagnostics["distributionSupplemented"] is True
    assert len(positions) == 4
    assert positions[0] < len(reply) * 0.35
    assert positions[-1] > len(reply) * 0.65


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


def test_refusal_is_not_read_as_a_request() -> None:
    """"不要发表情包" contains "要发表情包".

    The opt-in pattern matched the tail of the refusal, and an apparent opt-in
    used to cancel an explicit opt-out -- so asking for no stickers delivered
    stickers. Only a catalog request may override a refusal now.
    """
    prefs = StickerPreferences(frequency=100, intensity=100, max_per_reply=4, repeat_count=1)

    def allowed(text: str) -> bool:
        scene = analyze_scene(StickerContext(user_text=text, streaming=True), prefs)
        return bool(scene["allowOutput"])

    for refusal in ("这次不要发表情包", "别发表情包了", "禁止使用表情包",
                    "不使用表情包", "不需要表情包", "no stickers please"):
        assert allowed(refusal) is False, refusal

    for wanted in ("请发表情包", "给我看看全部表情包", "展示一下表情包目录", "继续"):
        assert allowed(wanted) is True, wanted

    # A negative that is not about stickers must not read as a refusal.
    assert allowed("这个不错，发个表情包") is True
