from __future__ import annotations

import re

from app.agent_runtime import AgentRuntime, BalancedStickerStreamingAgentRuntime
from app.agent_runtime.sticker_body_runtime import ensure_balanced_sticker_coverage
from app.agent_runtime.stickers import (
    INLINE_STICKER_VISIBLE_MARKER_RE,
    StickerContext,
    StickerPreferences,
    StickerResult,
    extract_keys,
)


def _result(text: str) -> StickerResult:
    return StickerResult(text=text, keys=tuple(extract_keys(text)), diagnostics={})


def _reasoning(text: str) -> str:
    lower = text.casefold()
    opening = lower.find("<think>")
    closing = lower.find("</think>", opening + len("<think>")) if opening >= 0 else -1
    if opening < 0:
        return ""
    start = opening + len("<think>")
    return text[start:closing if closing >= 0 else len(text)]


def _visible_answer(text: str) -> str:
    lower = text.casefold()
    opening = lower.find("<think>")
    if opening < 0:
        return text
    closing = lower.find("</think>", opening + len("<think>"))
    if closing < 0:
        return text[:opening]
    return text[:opening] + text[closing + len("</think>"):]


def test_production_runtime_uses_balanced_sticker_coverage() -> None:
    assert AgentRuntime is BalancedStickerStreamingAgentRuntime


def test_substantive_visible_body_gets_a_sticker_even_without_model_candidate() -> None:
    text = (
        "可以的，我现在具备完整的项目操作能力。\n\n"
        "- 新建项目：创建目录结构并初始化配置文件。\n"
        "- 文件管理：在工作空间内移动、重命名和删除文件。\n"
        "- 运行命令：执行构建、测试以及常用脚本。\n\n"
        "告诉我具体想做什么，我就可以直接开始。"
    )
    guarded = ensure_balanced_sticker_coverage(
        _result(text),
        StickerPreferences(frequency=50, intensity=50, max_per_reply=0, repeat_count=1),
        StickerContext(user_text="你可以做什么", streaming=True),
    )

    assert len(extract_keys(guarded.text)) >= 1
    assert guarded.diagnostics["bodyStickerGuaranteeApplied"] is True


def test_reasoning_sticker_is_preserved_and_body_still_gets_coverage() -> None:
    text = (
        "<think>我先分析一下这里最适合怎么回答，并且把几个关键判断逐项整理清楚。"
        "[[AI_LEDGER_INLINE_STICKER:thinking_soft]]</think>\n\n"
        "可以直接处理。当前正文已经有完整的结论，并且还会继续给出具体操作步骤。\n"
        "接下来我会按你的要求执行，而不是只给一个建议。"
    )
    guarded = ensure_balanced_sticker_coverage(
        _result(text),
        StickerPreferences(frequency=50, intensity=50, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="继续", streaming=True),
    )

    assert INLINE_STICKER_VISIBLE_MARKER_RE.search(_reasoning(guarded.text)) is not None
    assert INLINE_STICKER_VISIBLE_MARKER_RE.search(_visible_answer(guarded.text)) is not None
    assert guarded.diagnostics["reasoningStickerCount"] >= 1
    assert guarded.diagnostics["visibleBodyStickerCount"] >= 1


def test_substantive_reasoning_gets_a_sticker_when_body_already_has_one() -> None:
    text = (
        "<think>这里先检查输入条件，再比较两个实现方向，最后确认哪个方案最稳妥。"
        "还需要继续验证边界情况，避免只看表面现象就下结论。</think>\n"
        "已经确认可以继续。[[AI_LEDGER_INLINE_STICKER:confirm_yes]]\n"
        "下一步直接执行即可。"
    )
    guarded = ensure_balanced_sticker_coverage(
        _result(text),
        StickerPreferences(frequency=75, intensity=50, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="继续", streaming=True),
    )

    assert INLINE_STICKER_VISIBLE_MARKER_RE.search(_reasoning(guarded.text)) is not None
    assert extract_keys(_visible_answer(guarded.text)) == ["confirm_yes"]
    assert guarded.diagnostics["reasoningStickerGuaranteeApplied"] is True


def test_small_explicit_limit_keeps_final_answer_priority() -> None:
    text = (
        "<think>这里有一段完整的思考过程，可以自然放一个表情。"
        "[[AI_LEDGER_INLINE_STICKER:thinking_soft]]</think>\n"
        "最终正文也已经形成完整结论，而且正文必须优先保证至少有一个表情位置。"
    )
    guarded = ensure_balanced_sticker_coverage(
        _result(text),
        StickerPreferences(frequency=100, intensity=50, max_per_reply=1, repeat_count=1),
        StickerContext(user_text="继续", streaming=True),
    )

    assert len(extract_keys(guarded.text)) == 1
    assert INLINE_STICKER_VISIBLE_MARKER_RE.search(_visible_answer(guarded.text)) is not None
    assert INLINE_STICKER_VISIBLE_MARKER_RE.search(_reasoning(guarded.text)) is None
    assert guarded.diagnostics["reasoningMarkersRebalancedToAnswer"] == 1


def test_opt_out_and_frequency_zero_remain_hard_boundaries() -> None:
    text = "这是足够长的正文内容，可以形成完整自然表达节点，并且适合作为普通回复。"

    opted_out = ensure_balanced_sticker_coverage(
        _result(text),
        StickerPreferences(frequency=100, intensity=50, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="这次不要发表情包", streaming=True),
    )
    disabled = ensure_balanced_sticker_coverage(
        _result(text),
        StickerPreferences(frequency=0, intensity=50, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="继续", streaming=True),
    )

    assert extract_keys(opted_out.text) == []
    assert extract_keys(disabled.text) == []


def test_code_only_reply_is_not_forced_into_an_unsafe_anchor() -> None:
    text = "```python\nprint('hello world')\n```"
    guarded = ensure_balanced_sticker_coverage(
        _result(text),
        StickerPreferences(frequency=100, intensity=50, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="给我代码", streaming=True),
    )

    assert extract_keys(guarded.text) == []
    assert guarded.diagnostics.get("bodyStickerGuaranteeApplied") is not True
    assert re.search(r"AI_LEDGER_INLINE_STICKER", guarded.text) is None
