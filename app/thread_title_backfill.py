from __future__ import annotations

"""Compatibility shim for older title-backfill imports.

Automatic titles now use the single state machine in :mod:`app.thread_title_override`.
A neutral provisional label is shown while a detached model request generates a
real task title after the active turn, and legacy fallback titles are migrated by
that same path when a thread is opened or used again.
"""


def install() -> None:
    return None


def patch(_module) -> None:
    return None
