"""Compact presentation for the desktop conversation library.

The durable thread list behavior remains in ``widgets`` and ``window``.  This
module only changes how one conversation row is presented so the sidebar reads
like a mature recent-conversations list instead of a stack of tall cards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from app.desktop import format as fmt
from app.desktop import widgets as base


_ROW_HEIGHT = 40
_ATTENTION_STATES = {
    "running": "running",
    "starting": "running",
    "waiting_approval": "waiting_approval",
    "failed": "failed",
    "cancelled": "failed",
}


class ThreadListItemWidget(QWidget):
    """A single compact conversation row.

    Secondary information is retained in the tooltip rather than consuming a
    second visible line.  Attention states still keep their dot, so the tighter
    layout does not hide anything that requires action.
    """

    def __init__(
        self,
        record: dict[str, Any],
        parent: QWidget | None = None,
        *,
        active_workspace: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        del active_workspace  # kept for the stable constructor contract

        self.setObjectName("threadItemWidget")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._full_title = fmt.text(record.get("title")).strip() or "New conversation"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 0, 9, 0)
        layout.setSpacing(7)

        self.marker = QFrame(self)
        self.marker.setObjectName("threadRowMarker")
        self.marker.setFixedSize(2, 18)
        layout.addWidget(self.marker, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QLabel(self._full_title, self)
        self.title_label.setObjectName("threadItemTitle")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)

        state = "" if record.get("archived") else fmt.text(record.get("status"))
        attention = _ATTENTION_STATES.get(state, "")

        self.status_dot = QLabel("●", self)
        self.status_dot.setObjectName("threadDot")
        self.status_dot.setProperty("state", attention)
        self.status_dot.setVisible(bool(attention))
        layout.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)

        # Keep the metadata label as part of the public widget surface for tests
        # and callers, but move its information into the tooltip so rows stay on
        # one visual line.
        when = fmt.relative_time(record.get("updatedAt"))
        status_text = fmt.human_status(state) if attention else ""
        self.meta_label = QLabel(" · ".join(part for part in (when, status_text) if part), self)
        self.meta_label.setObjectName("threadItemMeta")
        self.meta_label.setProperty("state", attention)
        self.meta_label.setTextFormat(Qt.TextFormat.PlainText)
        self.meta_label.hide()

        detail = " · ".join(
            part
            for part in (
                when,
                fmt.short_path(record.get("workspace")) if record.get("workspace") else "",
                fmt.human_status(record.get("status")),
                f"{fmt.format_tokens((record.get('usage') or {}).get('totalTokens'))} tokens"
                if isinstance(record.get("usage"), dict)
                and (record.get("usage") or {}).get("totalTokens")
                else "",
            )
            if part
        )
        self.setToolTip(f"{self._full_title}\n{detail}" if detail else self._full_title)

    def set_active(self, active: bool) -> None:
        """Mark the row whose thread is currently open."""
        for widget in (self.marker, self.title_label):
            if widget.property("active") != active:
                widget.setProperty("active", active)
                base.repolish(widget)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        reserved = 48 if self.status_dot.isVisible() else 31
        available = max(72, self.width() - reserved)
        self.title_label.setText(
            self.title_label.fontMetrics().elidedText(
                self._full_title,
                Qt.TextElideMode.ElideRight,
                available,
            )
        )


def thread_row_size(widget: QWidget) -> QSize:
    """Use a single-line sidebar density instead of the legacy 52px row."""
    return QSize(0, _ROW_HEIGHT)


__all__ = ["ThreadListItemWidget", "thread_row_size"]
