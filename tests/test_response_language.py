from __future__ import annotations

from app.agent_runtime.response_language import communication_language_message, infer_user_language
from app.ai import AIMessage, MessageRole


def user(text: str) -> AIMessage:
    return AIMessage(role=MessageRole.USER, content=text)


def assistant(text: str) -> AIMessage:
    return AIMessage(role=MessageRole.ASSISTANT, content=text)


def test_chinese_language_survives_english_assistant_status_and_logs():
    messages = [
        user("继续深入定位这个问题，先看日志再检查源码。"),
        assistant("Running diagnostics and reading events.jsonl"),
        assistant("Now let me inspect ufo_sidecar.py and dispatch."),
    ]

    assert infer_user_language(messages) == "zh"
    anchor = communication_language_message(messages)
    assert anchor.name == "loom_communication_language"
    assert "Current user communication language: Chinese" in anchor.content
    assert "Tool output, logs" in anchor.content


def test_short_latin_acknowledgement_does_not_flip_chinese_thread():
    messages = [
        user("继续检查这个问题。"),
        user("ok"),
    ]

    assert infer_user_language(messages) == "zh"


def test_durable_fallback_survives_when_compaction_leaves_only_short_ack():
    messages = [user("ok")]

    assert infer_user_language(messages, fallback="zh") == "zh"
    anchor = communication_language_message(messages, fallback="zh")
    assert "Current user communication language: Chinese" in anchor.content


def test_technical_inline_code_does_not_override_chinese_language():
    messages = [
        user("继续看 `ufo.imports.started` 之后具体发生了什么。"),
    ]

    assert infer_user_language(messages) == "zh"


def test_substantive_english_user_turn_can_switch_language():
    messages = [
        user("继续检查这个问题。"),
        user("Please continue the investigation in English from this point."),
    ]

    assert infer_user_language(messages) == "latin"


def test_language_inference_ignores_non_user_messages():
    messages = [
        user("继续检查。"),
        assistant("This is a long English diagnostic explanation with many English words."),
    ]

    assert infer_user_language(messages) == "zh"
