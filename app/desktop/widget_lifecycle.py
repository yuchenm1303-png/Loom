"""Prevent transient top-level windows while desktop widgets are retired.

Qt promotes a visible QWidget to a native top-level window when ``setParent(None)``
is called. Loom has several deferred-delete reconciliation paths that historically
detached widgets immediately before ``deleteLater()``. On Windows, that tiny gap
is enough to flash a separate blank window titled "Loom".

The first guard covered transcript message/card widgets only. Runtime Activity
rows use the exact same detach/delete pattern, so rapid model-step notifications
could still promote an ``ActivityEventRow`` into the floating window seen in the
UI (for example a row reading ``Asked model · step 6``). Keep every disposable
reconciliation widget parented and hidden until Qt processes its deferred delete.
"""

from __future__ import annotations

from typing import Any

from app.desktop import widgets as base


_INSTALLED = False


def _guard_disposal_detach(cls: type[Any]) -> None:
    if getattr(cls, "_loom_disposal_detach_guard", False):
        return

    original = cls.setParent

    def set_parent(self: Any, parent: Any, *args: Any) -> Any:
        if parent is None and self.parentWidget() is not None:
            # All guarded call sites are reconciliation/disposal paths followed
            # by deleteLater(). Hide first so the old geometry cannot bleed
            # through, but preserve QObject/QWidget ownership until deletion.
            self.hide()
            return None
        return original(self, parent, *args)

    cls.setParent = set_parent
    cls._loom_disposal_detach_guard = True


def install() -> None:
    """Guard every custom widget retired by transcript/runtime reconciliation."""

    global _INSTALLED
    if _INSTALLED:
        return

    guarded = (
        # Conversation transcript reconciliation.
        base.RichLabel,
        base.CodeBlock,
        base.MessageWidget,
        base.ActivityCard,
        # Runtime Activity timeline reconciliation. These were missing from the
        # original fix and are the source of the remaining floating Loom window.
        base.ActivityEventRow,
        base.EmptyPanel,
    )
    for cls in guarded:
        _guard_disposal_detach(cls)
    _INSTALLED = True


__all__ = ["install"]
