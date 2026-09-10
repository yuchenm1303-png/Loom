from __future__ import annotations

"""Compatibility shim for the retired title rescue layer.

The current title path is intentionally simple and Codex-like: derive a readable
title from the first user prompt immediately, then let a detached model request
refine it when available. There is no long-lived pending title state to rescue.
"""


def install() -> None:
    return None


def patch(_module) -> None:
    return None
