from __future__ import annotations

import re

from app.agent_runtime import AgentRuntime, BodyFirstStickerStreamingAgentRuntime
from app.agent_runtime.sticker_body_runtime import ensure_visible_body_sticker
from app.agent_runtime.stickers import (
    INLINE_STICKER_VISIBLE_MARKER_RE,
    StickerContext,
    StickerPreferences,
    StickerResult,
    extract_keys,
)


def _result(text: str) -> StickerResult:
    return StickerResult(text=text, keys=tuple(extract_keys(text)), diagnostics={})


def _visible_answer(text: str) -> str:
    lower = text.casefold()
    opening = lower.find("<think>")
    if opening < 0:
        return text
    closing = lower.find("</think>", opening + len("<think>"))
    if closing < 0:
        return text[:opening]
    return text[:opening] + text[closing + len("</think>"):]


def test_production_runtime_uses_body_first_sticker_guard() -> None:
    assert AgentRuntime is BodyFirstStickerStreamingAgentRuntime


def test_substantive_visible_body_gets_a_sticker_even_without_model_candidate() -> None:
    text = (
        "可以的，我现在具备完整的项目操作能力。\n\n"
        "- 新建项目：创建目录结构并初始化配置文件。\n"
        "- 文件管理：在工作空间内移动、重命名和删除文件。\n"
        "- 运行命令：执行构建、测试以及常用脚本。\n\n"
        "告诉我具体想做什么，我就可以直接开始。"
    )
    guarded = ensure_visible_body_sticker(
        _result(text),
        StickerPreferences(frequency=50, intensity=50, max_per_reply=0, repeat_count=1),
        StickerContext(user_text="你可以做什么", streaming=True),
    )

    assert len(extract_keys(guarded.text)) >= 1
    assert guarded.diagnostics["bodyStickerGuaranteeApplied"] is True


def test_reasoning_sticker_does_not_satisfy_visible_body_guarantee() -> None:
    text = (
        "<think>我先分析一下这里最适合怎么回答。"
        "[[AI_LEDGER_INLINE_STICKER:thinking_soft]]</think>\n\n"
        "可以直接处理。当前正文已经有完整的结论，并且还会继续给出具体操作步骤。\n"
        "接下来我会按你的要求执行，而不是只给一个建议。"
    )
    guarded = ensure_visible_body_sticker(
        _result(text),
        StickerPreferences(frequency=50, intensity=50, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="继续", streaming=True),
    )

    reasoning = guarded.text.split("</think>", 1)[0]
    visible = _visible_answer(guarded.text)
    assert INLINE_STICKER_VISIBLE_MARKER_RE.search(reasoning) is None
    assert INLINE_STICKER_VISIBLE_MARKER_RE.search(visible) is not None
    assert guarded.diagnostics["reasoningStickerMarkersSuppressed"] == 1


def test_existing_visible_body_sticker_is_kept_without_forcing_an_extra_one() -> None:
    text = (
        "<think>这里是内部思考，不应该消费正文表情预算。</think>\n"
        "已经确认可以继续。[[AI_LEDGER_INLINE_STICKER:confirm_yes]]\n"
        "下一步直接执行即可。"
    )
    guarded = ensure_visible_body_sticker(
        _result(text),
        StickerPreferences(frequency=50, intensity=50, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="继续", streaming=True),
    )

    assert extract_keys(_visible_answer(guarded.text)) == ["confirm_yes"]
    assert guarded.diagnostics.get("bodyStickerGuaranteeApplied") is not True


def test_opt_out_and_frequency_zero_remain_hard_boundaries() -> None:
    text = "这是足够长的正文内容，可以形成完整自然表达节点，并且适合作为普通回复。"

    opted_out = ensure_visible_body_sticker(
        _result(text),
        StickerPreferences(frequency=100, intensity=50, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="这次不要发表情包", streaming=True),
    )
    disabled = ensure_visible_body_sticker(
        _result(text),
        StickerPreferences(frequency=0, intensity=50, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="继续", streaming=True),
    )

    assert extract_keys(opted_out.text) == []
    assert extract_keys(disabled.text) == []


def test_code_only_reply_is_not_forced_into_an_unsafe_anchor() -> None:
    text = "```python\nprint('hello world')\n```"
    guarded = ensure_visible_body_sticker(
        _result(text),
        StickerPreferences(frequency=100, intensity=50, max_per_reply=4, repeat_count=1),
        StickerContext(user_text="给我代码", streaming=True),
    )

    assert extract_keys(guarded.text) == []
    assert guarded.diagnostics.get("bodyStickerGuaranteeApplied") is not True
    assert re.search(r"AI_LEDGER_INLINE_STICKER", guarded.text) is None
