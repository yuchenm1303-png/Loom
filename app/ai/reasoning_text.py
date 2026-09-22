from __future__ import annotations

from dataclasses import dataclass, field


_OPEN_TAGS = ("<think>", "<thinking>")
_CLOSE_TAGS = ("</think>", "</thinking>")


def _longest_tag_prefix_suffix(value: str, tags: tuple[str, ...]) -> int:
    lowered = value.casefold()
    best = 0
    for tag in tags:
        candidate = tag.casefold()
        limit = min(len(lowered), len(candidate) - 1)
        for size in range(1, limit + 1):
            if lowered.endswith(candidate[:size]):
                best = max(best, size)
    return best


def _first_tag(value: str, tags: tuple[str, ...]) -> tuple[int, int] | None:
    lowered = value.casefold()
    found: tuple[int, int] | None = None
    for tag in tags:
        index = lowered.find(tag.casefold())
        if index >= 0 and (found is None or index < found[0]):
            found = (index, len(tag))
    return found


@dataclass(slots=True)
class InlineReasoningDemux:
    """Split provider-inline thinking markup across arbitrary stream chunks."""

    in_reasoning: bool = False
    _pending: str = ""
    _saw_reasoning: bool = False

    def feed(self, fragment: str) -> list[tuple[str, str]]:
        self._pending += str(fragment or "")
        output: list[tuple[str, str]] = []
        while self._pending:
            tags = _CLOSE_TAGS if self.in_reasoning else _OPEN_TAGS
            match = _first_tag(self._pending, tags)
            channel = "reasoning" if self.in_reasoning else "text"
            if match is not None:
                index, tag_length = match
                if index:
                    output.append((channel, self._pending[:index]))
                self._pending = self._pending[index + tag_length :]
                self.in_reasoning = not self.in_reasoning
                if self.in_reasoning:
                    self._saw_reasoning = True
                continue

            held = _longest_tag_prefix_suffix(self._pending, tags)
            ready = self._pending[:-held] if held else self._pending
            self._pending = self._pending[-held:] if held else ""
            if ready:
                output.append((channel, ready))
            break
        return output

    def finish(self) -> list[tuple[str, str]]:
        if not self._pending:
            return []
        channel = "reasoning" if self.in_reasoning else "text"
        value = self._pending
        self._pending = ""
        return [(channel, value)]

    @property
    def saw_reasoning(self) -> bool:
        return self._saw_reasoning


def split_inline_reasoning(text: str) -> tuple[str, str]:
    demux = InlineReasoningDemux()
    pieces = demux.feed(str(text or "")) + demux.finish()
    public = "".join(value for channel, value in pieces if channel == "text")
    reasoning = "".join(value for channel, value in pieces if channel == "reasoning")
    if not demux.saw_reasoning:
        return str(text or ""), ""
    return public.strip(), reasoning.strip()


def merge_visible_reasoning(existing: str, inline: str) -> str:
    current = str(existing or "").strip()
    added = str(inline or "").strip()
    if not added:
        return current
    if not current:
        return added
    if added == current or added in current:
        return current
    if current in added:
        return added
    return current + "\n\n" + added


__all__ = ["InlineReasoningDemux", "merge_visible_reasoning", "split_inline_reasoning"]
