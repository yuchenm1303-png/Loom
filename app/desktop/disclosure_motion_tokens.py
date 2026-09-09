"""Shared motion tokens for transcript disclosure interactions.

Thought process and task details should feel like the same product.  Keep their
motion vocabulary here so timing changes cannot drift across presentation hooks.
"""

from __future__ import annotations


DISCLOSURE_OPEN_MS = 175
DISCLOSURE_CLOSE_MS = 125
CHEVRON_OPEN_MS = 130
CHEVRON_CLOSE_MS = 105
CONTENT_REVEAL_MS = 145
CONTENT_OFFSET_PX = 3

__all__ = [
    "DISCLOSURE_OPEN_MS",
    "DISCLOSURE_CLOSE_MS",
    "CHEVRON_OPEN_MS",
    "CHEVRON_CLOSE_MS",
    "CONTENT_REVEAL_MS",
    "CONTENT_OFFSET_PX",
]
