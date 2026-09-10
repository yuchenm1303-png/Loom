from __future__ import annotations

"""Compatibility shim for older imports.

Thread titles are now owned by :mod:`app.thread_title_override`: Loom writes a
safe first-prompt title immediately and only uses the model for optional
background refinement. Keeping this module as a no-op preserves import
compatibility without reintroducing the old visible ``生成标题中…`` state.
"""


def install() -> None:
    return None


def patch(_module) -> None:
    return None
