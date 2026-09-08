"""A small, safe Markdown subset for the native transcript.

Assistant text is split into blocks rather than one HTML string: prose becomes
rich-text labels and fenced code becomes a real code widget with its own copy
action. Everything is escaped before any markup is added.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass


_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
_STRIKE = re.compile(r"~~(.+?)~~")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_FENCE = re.compile(r"^\s*```([^`]*)\s*$")
_HEADING = re.compile(r"^\s*(#{1,3})\s+(.+)$")
_LIST = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.+)$")
_CHECK = re.compile(r"^\[([ xX])\]\s+(.+)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_RULE = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$")


@dataclass(frozen=True, slots=True)
class Block:
    """One renderable unit of a message."""

    kind: str  # "rich" | "code"
    html: str = ""
    language: str = ""
    source: str = ""


def inline_html(value: str) -> str:
    """Escape, then apply inline markup. Code spans are protected from it."""
    safe = html.escape(value, quote=True)
    slots: dict[str, str] = {}

    def hold(markup: str) -> str:
        key = f"\x00LOOM{len(slots)}\x00"
        slots[key] = markup
        return key

    safe = _CODE.sub(lambda m: hold(f"<code>{m.group(1)}</code>"), safe)
    safe = _LINK.sub(
        lambda m: hold(f'<a href="{m.group(2)}">{m.group(1)}</a>'),
        safe,
    )
    safe = _BOLD.sub(r"<strong>\1</strong>", safe)
    safe = _STRIKE.sub(r"<s>\1</s>", safe)
    safe = _ITALIC.sub(r"<em>\1</em>", safe)
    for key, markup in slots.items():
        safe = safe.replace(key, markup)
    return safe


def parse_blocks(value: str) -> list[Block]:
    """Split Markdown-ish text into rich-text and code blocks, in order."""
    blocks: list[Block] = []
    chunks: list[str] = []
    paragraph: list[str] = []
    quote: list[str] = []
    list_items: list[str] = []
    list_kind: str | None = None
    code: list[str] = []
    language = ""
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            chunks.append("<p>" + "<br>".join(inline_html(x) for x in paragraph) + "</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_kind
        if not list_kind:
            return
        rows: list[str] = []
        for raw in list_items:
            checked = _CHECK.match(raw.strip())
            if checked:
                done = checked.group(1).casefold() == "x"
                mark = "✓" if done else "○"
                state = "done" if done else "todo"
                body = f"<span class='check {state}'>{mark}</span> {inline_html(checked.group(2))}"
            else:
                body = inline_html(raw)
            rows.append(f"<li>{body}</li>")
        chunks.append(f"<{list_kind}>" + "".join(rows) + f"</{list_kind}>")
        list_items.clear()
        list_kind = None

    def flush_quote() -> None:
        if quote:
            chunks.append(
                "<blockquote>" + "<br>".join(inline_html(x) for x in quote) + "</blockquote>"
            )
            quote.clear()

    def flush_text() -> None:
        flush_paragraph()
        flush_list()
        flush_quote()

    def emit_rich() -> None:
        flush_text()
        if chunks:
            blocks.append(Block(kind="rich", html="".join(chunks)))
            chunks.clear()

    def emit_code() -> None:
        blocks.append(Block(kind="code", language=language.strip(), source="\n".join(code)))
        code.clear()

    for line in value.splitlines():
        fence = _FENCE.match(line)
        if fence:
            if in_code:
                emit_code()
                language = ""
                in_code = False
            else:
                emit_rich()
                in_code = True
                language = fence.group(1)
            continue
        if in_code:
            code.append(line)
            continue
        if not line.strip():
            flush_text()
            continue
        if _RULE.match(line):
            flush_text()
            chunks.append("<div class='rule'></div>")
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_text()
            level = len(heading.group(1))
            chunks.append(f"<div class='h{level}'>{inline_html(heading.group(2))}</div>")
            continue

        quoted = _QUOTE.match(line)
        if quoted:
            flush_paragraph()
            flush_list()
            quote.append(quoted.group(1))
            continue
        if quote:
            flush_quote()

        listed = _LIST.match(line)
        if listed:
            flush_paragraph()
            kind = "ol" if listed.group(1)[0].isdigit() else "ul"
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            list_items.append(listed.group(2))
            continue
        if list_kind:
            flush_list()
        paragraph.append(line)

    if in_code:
        # An unterminated fence is normal while a response is still streaming.
        emit_code()
    emit_rich()
    return blocks


THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"


def _drop_partial_open_tag(value: str) -> str:
    """Hold back a half-arrived ``<think>`` so it never flashes as literal text.

    Deltas split anywhere, including inside the tag. Two or more characters of
    the opening tag are held; a lone ``<`` is left alone because prose uses it.
    """
    for size in range(len(THINK_OPEN) - 1, 1, -1):
        if value.endswith(THINK_OPEN[:size]):
            return value[:-size]
    return value


def split_reasoning(value: str) -> tuple[str, str, bool]:
    """Separate a reasoning model's private thinking from what it is saying.

    Models such as MiniMax-M2 stream their chain of thought inside the ordinary
    ``content`` field, wrapped in ``<think>`` tags, rather than in a separate
    reasoning field. Returns ``(reasoning, visible, live)``, where ``live`` marks
    thinking that has not been closed yet -- the normal mid-stream state.

    Nothing is discarded here: the durable message keeps its original text, and
    reasoning models need their own thinking in the history they are replayed.
    """
    text = value or ""
    reasoning: list[str] = []
    visible: list[str] = []
    live = False

    rest = text
    while rest:
        start = rest.find(THINK_OPEN)
        close = rest.find(THINK_CLOSE)
        if start < 0 and close < 0:
            visible.append(rest)
            break
        if close >= 0 and (start < 0 or close < start):
            # A closing tag with no opening one: the reply began mid-thought.
            reasoning.append(rest[:close])
            rest = rest[close + len(THINK_CLOSE) :]
            continue
        visible.append(rest[:start])
        rest = rest[start + len(THINK_OPEN) :]
        end = rest.find(THINK_CLOSE)
        if end < 0:
            reasoning.append(rest)
            live = True
            break
        reasoning.append(rest[:end])
        rest = rest[end + len(THINK_CLOSE) :]

    body = _drop_partial_open_tag("".join(visible)).strip()
    thinking = "\n\n".join(part.strip() for part in reasoning if part.strip())
    return thinking, body, live


def render_html(value: str) -> str:
    """Whole-message HTML, used where a single rich-text string is required."""
    parts: list[str] = []
    for block in parse_blocks(value):
        if block.kind == "code":
            label = html.escape(block.language, quote=True)
            badge = f"<div class='codeLabel'>{label}</div>" if label else ""
            body = html.escape(block.source, quote=False)
            parts.append(f"<div class='codeBlock'>{badge}<pre>{body}</pre></div>")
        else:
            parts.append(block.html)
    return "".join(parts) or "<p></p>"


__all__ = [
    "Block",
    "THINK_CLOSE",
    "THINK_OPEN",
    "inline_html",
    "parse_blocks",
    "render_html",
    "split_reasoning",
]
