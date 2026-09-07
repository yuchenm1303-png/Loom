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


__all__ = ["Block", "inline_html", "parse_blocks", "render_html"]
