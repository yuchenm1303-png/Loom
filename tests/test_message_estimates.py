"""The token estimate of a history is worked out once per message, not once per request.

A request is estimated several times a step, and each time the whole history was dumped to JSON (megabytes, tens of
milliseconds). The estimate is a third of the bytes of that JSON text, which is a sum of what each message adds, so the
share of each message is remembered while the message is in use. The number must stay exactly what dumping gave.
"""
from __future__ import annotations

import json
import math
import random

import pytest

from app.agent_runtime import context_budget
from app.agent_runtime.context_budget import estimate_tokens, estimate_tool_schema_tokens, message_estimates
from app.agent_runtime.context_composer import _history_identity, _identities, request_metadata
from app.agent_runtime.message_memo import MessageMemo
from app.agent_runtime.storage import _message_to_dict
from app.ai import AIMessage, ImagePart, MessageRole, TextPart, ToolCall, ToolDefinition


def reference_estimate(messages, tools=()) -> int:
    """The estimator as it was: dump everything, count the bytes."""
    data = []
    image_count = 0
    for message in messages:
        payload = _message_to_dict(message)
        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "image":
                    continue
                image_count += 1
                image_url = str(item.get("image_url") or "")
                metadata, separator, _encoded = image_url.partition(",")
                if separator and metadata.casefold().startswith("data:image/") and ";base64" in metadata.casefold():
                    item["image_url"] = metadata + separator
        data.append(payload)
    schemas = [{"name": tool.name, "description": tool.description, "parameters": tool.input_schema} for tool in tools]
    text = json.dumps([data, schemas], ensure_ascii=False)
    return math.ceil(len(text.encode("utf-8")) / 3) + 8 * len(messages) + image_count * 2048


_WORDS = ["alpha", "bêta", "γάμμα", "日本語のテキスト", "emoji 🙂🚀", 'quote " and \\ backslash', "line\nbreak\ttab", "ctrl\x01\x1f", "x" * 300, ""]
_IMAGES = ["data:image/png;base64,AAAA" + "B" * 500, "https://example.invalid/a.png", "data:text/plain,hello", "data:image/png;base64", "DATA:IMAGE/JPEG;BASE64,CCCC"]


def random_history(rng: random.Random, size: int) -> list[AIMessage]:
    messages: list[AIMessage] = []
    open_calls: list[str] = []
    for index in range(size):
        pick = rng.randrange(7)
        text = " ".join(rng.choice(_WORDS) for _ in range(rng.randrange(1, 5))) or "x"
        if pick == 0:
            messages.append(AIMessage(MessageRole.USER, text))
        elif pick == 1:
            messages.append(AIMessage(MessageRole.SYSTEM, text, name=rng.choice(["", "loom_note"])))
        elif pick == 2:
            parts = [TextPart(text)]
            for _ in range(rng.randrange(1, 3)):
                parts.append(ImagePart(rng.choice(_IMAGES)))
            messages.append(AIMessage(MessageRole.USER, tuple(parts)))
        elif pick == 3:
            messages.append(AIMessage(MessageRole.ASSISTANT, text, reasoning=rng.choice(["", "because 🙂"]), phase=rng.choice([None, "final_answer"])))
        elif pick == 4:
            calls = tuple(ToolCall(f"c{index}-{n}", rng.choice(["exec", "read"]), {"argv": [text], "nested": {"k": [1, 2.5, None, True]}})
                          for n in range(rng.randrange(1, 3)))
            messages.append(AIMessage(MessageRole.ASSISTANT, rng.choice(["", text]), tool_calls=calls))
            open_calls.extend(call.call_id for call in calls)
        elif open_calls:
            call_id = open_calls.pop(rng.randrange(len(open_calls)))
            messages.append(AIMessage(MessageRole.TOOL, text, tool_call_id=call_id))
        else:
            messages.append(AIMessage(MessageRole.USER, text))
    return messages


def some_tools() -> list[ToolDefinition]:
    return [ToolDefinition(f"tool_{n}", "does a thing ✓ " * n, {"type": "object", "properties": {"a": {"type": "string", "description": "é" * n}}})
            for n in range(1, 6)]


@pytest.fixture(autouse=True)
def fresh_memos():
    context_budget._measurements.clear()
    _identities.clear()
    yield


@pytest.mark.parametrize("seed", range(8))
def test_the_estimate_is_what_dumping_the_history_gave(seed):
    rng = random.Random(seed)
    messages = random_history(rng, 60)
    tools = some_tools()
    for count in (0, 1, 2, 7, 30, 60):
        assert estimate_tokens(messages[:count]) == reference_estimate(messages[:count])
        assert estimate_tokens(messages[:count], tools) == reference_estimate(messages[:count], tools)
    # Again, now that the messages are remembered, and in another arrangement.
    shuffled = list(messages)
    rng.shuffle(shuffled)
    assert estimate_tokens(messages, tools) == reference_estimate(messages, tools)
    assert estimate_tokens(shuffled, tools) == reference_estimate(shuffled, tools)
    assert estimate_tokens(messages[10:40]) == reference_estimate(messages[10:40])


@pytest.mark.parametrize("seed", range(8))
def test_the_estimate_of_each_message_is_what_estimating_it_alone_gave(seed):
    messages = random_history(random.Random(100 + seed), 60)
    expected = [reference_estimate((message,)) for message in messages]
    assert message_estimates(messages) == expected
    assert message_estimates(messages) == expected  # remembered
    assert [estimate_tokens((message,)) for message in messages] == expected
    assert message_estimates([]) == []


def test_the_schema_estimate_is_unchanged():
    tools = some_tools()
    assert estimate_tool_schema_tokens(tools) == reference_estimate((), tools) - reference_estimate((), ())
    assert estimate_tool_schema_tokens(()) == 0


def test_the_layout_of_a_request_carries_the_same_estimates(tmp_path):
    from types import SimpleNamespace

    messages = random_history(random.Random(5), 40)
    layout = request_metadata(SimpleNamespace(), SimpleNamespace(messages=messages, request_context_frames=[]), messages, some_tools())
    assert [section["estimated_tokens"] for section in layout["sections"]] == [reference_estimate((m,)) for m in messages]
    assert [section["role"] for section in layout["sections"]] == [m.role.value for m in messages]


def test_a_message_is_measured_once_while_it_is_in_use(monkeypatch):
    messages = [AIMessage(MessageRole.USER, f"question {n}") for n in range(20)]
    measured = []
    real = context_budget._measure_message
    monkeypatch.setattr(context_budget, "_measure_message", lambda message: measured.append(message) or real(message))
    for _ in range(5):
        estimate_tokens(messages)
        message_estimates(messages)
    assert len(measured) == 20
    # a message that only looks the same is its own
    estimate_tokens([AIMessage(MessageRole.USER, "question 0")])
    assert len(measured) == 21


def test_a_call_without_its_result_may_still_be_edited_and_is_measured_as_it_is(monkeypatch):
    """A tool handler is handed the arguments of the call it runs and may edit them until the call has its result."""
    call = ToolCall("run-1", "exec", {"argv": ["echo"]})
    asking = AIMessage(MessageRole.ASSISTANT, "", tool_calls=(call,))
    before = estimate_tokens([AIMessage(MessageRole.USER, "go"), asking])
    call.arguments["argv"] = ["echo"] + ["more text"] * 400
    unfinished = estimate_tokens([AIMessage(MessageRole.USER, "go"), asking])
    assert unfinished > before and unfinished == reference_estimate([AIMessage(MessageRole.USER, "go"), asking])
    assert message_estimates([asking])[0] == reference_estimate([asking])
    # once the result is there the call is finished, and the estimate is as the arguments stand
    answered = [AIMessage(MessageRole.USER, "go"), asking, AIMessage(MessageRole.TOOL, "done", tool_call_id="run-1")]
    assert estimate_tokens(answered) == reference_estimate(answered)
    measured = []
    real = context_budget._measure_message
    monkeypatch.setattr(context_budget, "_measure_message", lambda message: measured.append(message) or real(message))
    for _ in range(3):
        assert estimate_tokens(answered) == reference_estimate(answered)
    assert measured == []  # all three were measured before; nothing is measured again


def test_a_call_with_one_result_missing_is_not_finished():
    calls = (ToolCall("a", "exec", {"x": 1}), ToolCall("b", "exec", {"x": 2}))
    asking = AIMessage(MessageRole.ASSISTANT, "", tool_calls=calls)
    partly = [asking, AIMessage(MessageRole.TOOL, "one", tool_call_id="a")]
    calls[1].arguments["x"] = "y" * 5000
    assert estimate_tokens(partly) == reference_estimate(partly)
    calls[1].arguments["x"] = "z" * 9000
    assert estimate_tokens(partly) == reference_estimate(partly)


def test_the_memo_is_bounded_by_entries_and_by_the_text_it_keeps_alive():
    memo = MessageMemo(entries=3, budget=10_000)
    messages = [AIMessage(MessageRole.USER, f"message {n}") for n in range(5)]
    for number, message in enumerate(messages):
        memo.put(message, number)
    assert len(memo) == 3 and memo.get(messages[0]) is None and memo.get(messages[4]) == 4
    # the least recently used goes first
    memo.get(messages[2])
    memo.put(AIMessage(MessageRole.USER, "one more"), 99)
    assert memo.get(messages[2]) == 2 and memo.get(messages[3]) is None
    heavy = AIMessage(MessageRole.USER, "x" * 50_000)
    memo.put(heavy, 1)
    assert memo.get(heavy) is None and memo.get(messages[2]) is None  # it pushed everything out, itself included
    memo.clear()
    assert len(memo) == 0


def test_a_memo_hit_is_the_same_message_and_not_the_same_address():
    memo = MessageMemo()
    message = AIMessage(MessageRole.USER, "hello")
    memo.put(message, "value")
    assert memo.get(message) == "value"
    assert memo.get(AIMessage(MessageRole.USER, "hello")) is None  # equal, not the same
    # And if another object ever were at the address an entry was made for, it would not be taken for that message.
    stranger = AIMessage(MessageRole.USER, "hello")
    memo._entries[id(stranger)] = (message, "value of the other message", 1)
    assert memo.get(stranger) is None
    # The entry holds the message, so its address cannot be handed to another object while it is remembered.
    address = id(message)
    del message
    import gc

    gc.collect()
    others = [AIMessage(MessageRole.USER, f"other {n}") for n in range(200)]
    assert all(id(other) != address for other in others)


def test_a_history_identity_is_worked_out_once(monkeypatch):
    from app.agent_runtime import context_composer

    worked = []
    real = context_composer._message_to_dict
    monkeypatch.setattr(context_composer, "_message_to_dict", lambda message: worked.append(message) or real(message))
    message = AIMessage(MessageRole.USER, "worked out once")
    first = _history_identity(message)
    assert [_history_identity(message) for _ in range(5)] == [first] * 5
    assert len(worked) == 1


def test_history_identities_are_what_they_were_and_are_remembered():
    messages = random_history(random.Random(9), 50)
    first = [_history_identity(message) for message in messages]
    _identities.clear()
    assert [_history_identity(message) for message in messages] == first
    assert len(_identities) == len(messages)
    assert len(set(first)) > 10
    # the identity of a call is its ids, not its arguments, which a request may reduce or repair
    a = AIMessage(MessageRole.ASSISTANT, "", tool_calls=(ToolCall("same", "exec", {"x": 1}),))
    b = AIMessage(MessageRole.ASSISTANT, "", tool_calls=(ToolCall("same", "read", {"y": 2}),))
    assert _history_identity(a) == _history_identity(b)
    assert _history_identity(AIMessage(MessageRole.USER, "one")) != _history_identity(AIMessage(MessageRole.USER, "two"))
