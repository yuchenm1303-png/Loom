"""Bounded, content-free observations of Chat Completions wire deltas.

These counters describe transport evidence; they never decide whether a task
is complete, reject output, or trigger another model request.
"""
from dataclasses import dataclass


@dataclass
class TextShape:
    chars: int = 0
    newlines: int = 0
    whitespace_chars: int = 0
    max_whitespace_run: int = 0
    _run: int = 0

    def observe(self, text: str) -> None:
        self.chars += len(text)
        self.newlines += text.count("\n")
        for char in text:
            if char.isspace():
                self.whitespace_chars += 1
                self._run += 1
                self.max_whitespace_run = max(self.max_whitespace_run, self._run)
            else:
                self._run = 0

    def snapshot(self) -> dict[str, int]:
        return {key: getattr(self, key) for key in
                ("chars", "newlines", "whitespace_chars", "max_whitespace_run")}


class ChatStreamDiagnostics:
    def __init__(self, max_output_tokens: int | None):
        self.raw_text = TextShape()
        self.normalized_text = TextShape()
        self.counts = dict(chunks=0, text_fragments=0, reasoning_fragments=0,
                           tool_fragments=0, tool_argument_fragments=0,
                           tool_argument_chars=0, usage_chunks=0,
                           no_choice_chunks=0, multiple_choice_chunks=0,
                           finish_markers=0)
        self.max_output_tokens = max_output_tokens

    def observe_chunk(self, chunk) -> None:
        self.counts["chunks"] += 1
        if getattr(chunk, "usage", None) is not None:
            self.counts["usage_chunks"] += 1
        choices = getattr(chunk, "choices", None) or ()
        if not choices:
            self.counts["no_choice_chunks"] += 1
            return
        if len(choices) > 1:
            self.counts["multiple_choice_chunks"] += 1
        choice = choices[0]
        if getattr(choice, "finish_reason", None):
            self.counts["finish_markers"] += 1
        delta = getattr(choice, "delta", None)
        text = str(getattr(delta, "content", "") or "")
        if text:
            self.counts["text_fragments"] += 1
            self.raw_text.observe(text)
        if getattr(delta, "reasoning_content", None):
            self.counts["reasoning_fragments"] += 1
        for call in getattr(delta, "tool_calls", None) or ():
            self.counts["tool_fragments"] += 1
            args = str(getattr(getattr(call, "function", None), "arguments", "") or "")
            if args:
                self.counts["tool_argument_fragments"] += 1
                self.counts["tool_argument_chars"] += len(args)

    def snapshot(self) -> dict:
        return {"schema": 1, "transport": "chat_completions",
                "requested_max_output_tokens": self.max_output_tokens,
                **self.counts, "raw_text": self.raw_text.snapshot(),
                "normalized_text": self.normalized_text.snapshot()}
