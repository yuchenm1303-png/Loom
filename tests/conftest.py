"""Shared helpers for the Loom test suite."""

from __future__ import annotations

import pytest

from app.agent_runtime.stickers import INLINE_STICKER_VISIBLE_MARKER_RE


def without_stickers(text: str) -> str:
    """Drop inline sticker markers from a reply before asserting on its wording.

    Loom deliberately injects [[AI_LEDGER_INLINE_STICKER:key]] markers into the
    visible answer body, and the client renders them. Tests that care about what
    the model said, rather than how it is decorated, compare the plain text so a
    change in sticker placement does not read as a behaviour change.
    """

    return INLINE_STICKER_VISIBLE_MARKER_RE.sub("", str(text or "")).strip()


@pytest.fixture
def plain_text():
    return without_stickers
