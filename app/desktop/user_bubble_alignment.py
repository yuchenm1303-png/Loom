"""Optically center outgoing message text inside its bubble.

Qt's rich-text line box reserves noticeably more invisible descent/leading below
short CJK text than above it. With mathematically equal outer padding the line
box is centered, but the visible glyphs sit several pixels high in the bubble.

Keep the bubble's total vertical inset unchanged and bias that inset so the
visible text, rather than the rich-text line box, is what appears centered. The
bias is only used for simple prose bubbles; code/mixed-content messages retain
symmetric padding so larger blocks are not disturbed.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from app.desktop import message_presentation as presentation
from app.desktop import widgets as base


_INSTALLED = False
_NORMAL_MARGINS = (14, 10, 14, 10)
# Screenshot-calibrated optical correction: same 20 px vertical budget as the
# normal margins, shifted down by 8 px to compensate for QTextDocument's visual
# line-box bias on short CJK/Latin prose.
_OPTICAL_MARGINS = (14, 18, 14, 2)


def _sync_user_alignment(message: Any) -> None:
    if getattr(message, "role", "") != "user":
        return

    layout = message.layout()
    if layout is None:
        return

    widgets = list(getattr(message, "_widgets", []) or [])
    simple_prose = len(widgets) == 1 and isinstance(widgets[0], base.RichLabel)
    margins = _OPTICAL_MARGINS if simple_prose else _NORMAL_MARGINS
    layout.setContentsMargins(*margins)
    layout.setSpacing(0)

    # The horizontal centering was already correct; keep it explicit so future
    # rich-text changes cannot regress the simple outgoing-bubble contract.
    for widget in widgets:
        if isinstance(widget, base.RichLabel):
            widget.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)

    message.updateGeometry()


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_init = presentation.MessageWidget.__init__
    original_set_text = presentation.MessageWidget.set_text

    def message_init(self: Any, role: str, parent: QWidget | None = None) -> None:
        original_init(self, role, parent)
        _sync_user_alignment(self)

    def set_text(self: Any, value: str) -> None:
        original_set_text(self, value)
        _sync_user_alignment(self)

    presentation.MessageWidget.__init__ = message_init
    presentation.MessageWidget.set_text = set_text


__all__ = ["install"]
