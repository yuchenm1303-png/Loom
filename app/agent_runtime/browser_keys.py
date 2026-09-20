"""Physical-key descriptors for synthetic typing.

A character typed on a real keyboard arrives with the key it came from:
``KeyboardEvent.code`` names the physical key and ``keyCode`` carries the
Windows virtual-key code. Sending only ``key`` and ``text`` is enough for an
ordinary input, because the character insertion is driven by the text, and that
is why the gap went unnoticed for so long. It is not enough for a page that
reads the keyboard rather than a field.

noVNC is the case that forced this: it resolves ``code`` first, and an event
without one falls into its virtual-keyboard branch, which bypasses the key
tracking every other key goes through. ``browser_press`` always named the
physical key, ``browser_send_text`` never did, so on a VNC console single key
presses arrived and typed text did not.

Mappings are US layout. Anything outside it - CJK, emoji - has no physical key
to name, and inventing one would be worse than the text-only event those
characters already need, so they keep the old shape.
"""
from __future__ import annotations


# The smallest pace that a remote desktop survives. A canvas client forwards
# each key straight to the guest, and QEMU's emulated PS/2 keyboard has a
# shallow queue: a burst sent with no gap is dropped wholesale rather than
# delivered quickly. Real typing is paced, so this pauses between keys.
KEY_INTERVAL_SECONDS = 0.012

# ...but only for as long as the pacing can matter. A remote console receives
# commands, not essays; an 8,000 character paste belongs to an ordinary page
# that never needed the gap, and spending 96 seconds asleep to deliver it would
# trade one silent failure for another.
KEY_INTERVAL_BUDGET_SECONDS = 3.0


_SHIFTED_CHARACTERS = {
    "!": "1",
    "@": "2",
    "#": "3",
    "$": "4",
    "%": "5",
    "^": "6",
    "&": "7",
    "*": "8",
    "(": "9",
    ")": "0",
    "_": "-",
    "+": "=",
    "{": "[",
    "}": "]",
    "|": "\\",
    ":": ";",
    '"': "'",
    "~": "`",
    "<": ",",
    ">": ".",
    "?": "/",
}

_PUNCTUATION_KEYS = {
    " ": ("Space", 32),
    "-": ("Minus", 189),
    "=": ("Equal", 187),
    "[": ("BracketLeft", 219),
    "]": ("BracketRight", 221),
    "\\": ("Backslash", 220),
    ";": ("Semicolon", 186),
    "'": ("Quote", 222),
    "`": ("Backquote", 192),
    ",": ("Comma", 188),
    ".": ("Period", 190),
    "/": ("Slash", 191),
}

# Named keys send no text, so their virtual-key code is the only thing that
# keeps KeyboardEvent.keyCode from being 0.
NAMED_KEY_CODES = {
    "Enter": 13,
    "Tab": 9,
    "Backspace": 8,
    "Escape": 27,
    "Delete": 46,
    "ArrowUp": 38,
    "ArrowDown": 40,
    "ArrowLeft": 37,
    "ArrowRight": 39,
    "Home": 36,
    "End": 35,
    "PageUp": 33,
    "PageDown": 34,
    "Insert": 45,
    "ContextMenu": 93,
}

SHIFT_MODIFIER = 8
SHIFT_VIRTUAL_KEY = 16


def character_key(char: str) -> tuple[str, int, bool]:
    """Physical key, Windows virtual-key code, and shift state for one character.

    Returns ``("", 0, False)`` for characters with no US-layout key.
    """

    base = _SHIFTED_CHARACTERS.get(char)
    shift = base is not None
    if base is None:
        base = char
    if "a" <= base <= "z":
        return f"Key{base.upper()}", ord(base.upper()), shift
    if "A" <= base <= "Z":
        return f"Key{base}", ord(base), True
    if "0" <= base <= "9":
        return f"Digit{base}", ord(base), shift
    mapped = _PUNCTUATION_KEYS.get(base)
    if mapped is None:
        return "", 0, shift
    return mapped[0], mapped[1], shift


__all__ = [
    "KEY_INTERVAL_BUDGET_SECONDS",
    "KEY_INTERVAL_SECONDS",
    "NAMED_KEY_CODES",
    "SHIFT_MODIFIER",
    "SHIFT_VIRTUAL_KEY",
    "character_key",
]
