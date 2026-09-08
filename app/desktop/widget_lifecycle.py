"""Prevent transient top-level windows while transcript widgets are retired.

Qt turns a visible QWidget into a top-level window when ``setParent(None)`` is
used. The transcript historically did that immediately before ``deleteLater``.
On Windows, rapid streaming/tool reconciliation can therefore flash a blank
window titled with the application name ("Loom") before the deferred delete is
processed. Keeping disposable transcript widgets parented until deletion avoids
that native-window promotion entirely.
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
            # The transcript's detach calls are disposal paths followed by
            # deleteLater(). Hiding and retaining the QObject parent is safer:
            # ownership remains valid and Qt never promotes the widget to a
            # native top-level window between those two operations.
            self.hide()
            return None
        return original(self, parent, *args)

    cls.setParent = set_parent
    cls._loom_disposal_detach_guard = True


def install() -> None:
    """Install the guard on widget types retired by transcript reconciliation."""

    global _INSTALLED
    if _INSTALLED:
        return
    for cls in (base.RichLabel, base.CodeBlock, base.MessageWidget, base.ActivityCard):
        _guard_disposal_detach(cls)
    _INSTALLED = True


__all__ = ["install"]
