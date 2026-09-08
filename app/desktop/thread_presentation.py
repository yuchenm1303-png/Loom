"""Compact presentation for the desktop conversation library.

The durable thread list behavior remains in ``widgets`` and ``window``. This
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

_ROW_QSS = """
QWidget#threadItemWidget {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 9px;
}
QWidget#threadItemWidget[active="true"] {
    background: #24262d;
    border-color: #30333b;
}
QLabel#threadItemTitle {
    background: transparent;
    color: #cbd0d9;
    font-size: 13px;
    font-weight: 520;
}
QLabel#threadItemTitle[active="true"] {
    color: #f4f5f7;
    font-weight: 640;
}
QLabel#threadDot {
    background: transparent;
    color: #7f8795;
    font-size: 8px;
}
QLabel#threadDot[state="running"] { color: #7fb2f5; }
QLabel#threadDot[state="waiting_approval"] { color: #e0b473; }
QLabel#threadDot[state="failed"] { color: #df8e98; }
"""


class ThreadListItemWidget(QWidget):
    """A single compact conversation row.

    Secondary information is retained in the tooltip rather than consuming a
    second visible line. Attention states still keep their dot, so the tighter
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
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("active", False)
        self.setStyleSheet(_ROW_QSS)
        self._full_title = fmt.text(record.get("title")).strip() or "New conversation"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(11, 0, 10, 0)
        layout.setSpacing(8)

        # Kept as a compatibility hook for callers/tests that inspect the old
        # selection marker. The visible selection is now the rounded row itself,
        # matching a modern recent-conversations list instead of a purple rail.
        self.marker = QFrame(self)
        self.marker.setObjectName("threadRowMarker")
        self.marker.setFixedSize(0, 0)
        self.marker.hide()

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
        active = bool(active)
        for widget in (self, self.marker, self.title_label):
            if widget.property("active") != active:
                widget.setProperty("active", active)
                base.repolish(widget)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        reserved = 36 if self.status_dot.isVisible() else 22
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
    del widget
    return QSize(0, _ROW_HEIGHT)


__all__ = ["ThreadListItemWidget", "thread_row_size"]
