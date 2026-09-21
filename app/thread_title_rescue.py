from __future__ import annotations

"""Compatibility shim for the retired title-rescue layer.

Retries, legacy migration, manual-rename race protection, and neutral fallback
display are all owned by :mod:`app.thread_title_override`. Keeping this module
as a no-op preserves compatibility without reintroducing a second title state
machine.
"""


def install() -> None:
    return None


def patch(_module) -> None:
    return None
