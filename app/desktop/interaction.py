"""Small native interactions that never animate text or layout geometry."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QPushButton, QSizePolicy
from app.desktop import theme


class SuggestionCard(QPushButton):
    """Keyboard-accessible starter card with a reversible hover transition."""
    def __init__(self, title: str, description: str, parent=None):
        super().__init__(title, parent)
        self.description = description
        self.setAccessibleName(title)
        self.setAccessibleDescription(description)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(92)
        self.setMinimumWidth(0)
        self._progress = 0.0
        self._motion = QVariantAnimation(self)
        self._motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._motion.valueChanged.connect(self._update_progress)

    def sizeHint(self):
        return QSize(180, 92)

    def minimumSizeHint(self):
        return QSize(0, 92)

    def _update_progress(self, value):
        self._progress = float(value)
        self.update()

    def _settle(self):
        target = 1.0 if self.underMouse() or self.hasFocus() else 0.0
        self._motion.stop()
        if not theme.motion_enabled():
            self._update_progress(target)
            return
        self._motion.setDuration(150 if target else 210)
        self._motion.setStartValue(self._progress)
        self._motion.setEndValue(target)
        self._motion.start()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._settle()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._settle()

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._settle()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._settle()

    def hideEvent(self, event):
        self._motion.stop()
        self._progress = 0.0
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setBrush(QColor("#20212d" if not self.isDown() else "#181921"))
        painter.setPen(QPen(QColor("#363747"), 1))
        painter.drawRoundedRect(rect, 14, 14)
        glow = QColor(theme.ACCENT)
        glow.setAlphaF(self._progress * 0.12)
        edge = QColor(theme.ACCENT_SOFT)
        edge.setAlphaF(self._progress * 0.75)
        painter.setBrush(glow)
        painter.setPen(QPen(edge, 1))
        painter.drawRoundedRect(rect, 14, 14)
        font = QFont("Segoe UI")
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_STRONG))
        title = painter.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, max(0, self.width()-56))
        painter.drawText(QRectF(16, 19, self.width()-52, 23), Qt.AlignmentFlag.AlignLeft, title)
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_MUTED))
        detail = painter.fontMetrics().elidedText(self.description, Qt.TextElideMode.ElideRight, max(0, self.width()-32))
        painter.drawText(QRectF(16, 49, self.width()-32, 22), Qt.AlignmentFlag.AlignLeft, detail)
        painter.setPen(QColor(theme.ACCENT_SOFT))
        painter.drawText(QRectF(self.width()-33 + 2*self._progress, 19-2*self._progress, 20, 22), Qt.AlignmentFlag.AlignCenter, "↗")


class FocusFrame(QFrame):
    """Animate only the input boundary, leaving editor glyphs pixel-stable."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._focus_progress = 0.0
        self._focus_motion = QVariantAnimation(self)
        self._focus_motion.setDuration(180)
        self._focus_motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._focus_motion.valueChanged.connect(self._update_focus)

    def _update_focus(self, value):
        self._focus_progress = float(value)
        self.update()

    def animate_focus(self, focused):
        self._focus_motion.stop()
        if not theme.motion_enabled():
            self._update_focus(float(focused))
            return
        self._focus_motion.setStartValue(self._focus_progress)
        self._focus_motion.setEndValue(float(focused))
        self._focus_motion.start()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._focus_progress <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(theme.ACCENT_SOFT)
        color.setAlphaF(self._focus_progress * 0.85)
        painter.setPen(QPen(color, 1.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 17, 17)
