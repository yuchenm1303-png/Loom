"""Codex-style model selector presentation for Loom's composer.

The model picker is a compact neutral pill: model name on the left, a quiet
native chevron on the right, and no decorative model icon. Behaviour remains in
``ComposerPanel._open_model_menu``; this module only replaces the presentation.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen

from app.desktop import theme
from app.desktop.composer import ControlButton


_INSTALLED = False

_MODEL_SELECTOR_QSS = r"""
QPushButton#composerModel {
    min-height:30px;
    max-height:30px;
    padding:0 28px 0 13px;
    border-radius:15px;
    background:#343439;
    border:1px solid transparent;
    color:#f0f0f2;
    font-size:11px;
    font-weight:600;
    text-align:left;
}
QPushButton#composerModel:hover {
    background:#3b3b40;
    border-color:#45454b;
    color:#ffffff;
}
QPushButton#composerModel:pressed {
    background:#2e2e33;
    border-color:#39393e;
}
QPushButton#composerModel:disabled {
    background:#29292d;
    border-color:transparent;
    color:#77777e;
}
"""


class ModelSelectorButton(ControlButton):
    """Model text plus a native trailing chevron; deliberately no leading icon."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(icon="", object_name="composerModel", parent=parent)
        self.setIcon(QIcon())
        self.setMinimumHeight(30)
        self.setMaximumHeight(30)

    def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            color = QColor("#66666d")
        elif self.underMouse():
            color = QColor("#c9c9ce")
        else:
            color = QColor("#9b9ba2")
        painter.setPen(
            QPen(
                color,
                1.3,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        x = float(self.width() - 14)
        y = self.height() / 2.0 - 0.5
        painter.drawLine(QPointF(x - 3.0, y - 1.8), QPointF(x, y + 1.4))
        painter.drawLine(QPointF(x, y + 1.4), QPointF(x + 3.0, y - 1.8))
        painter.end()


def _replace_model_button(panel: Any) -> None:
    old = panel.model_button
    outer = panel.layout()
    controls = outer.itemAt(1).layout() if outer is not None and outer.count() > 1 else None
    if controls is None:
        old.setIcon(QIcon())
        old.setObjectName("composerModel")
        return

    index = controls.indexOf(old)
    replacement = ModelSelectorButton(panel)
    replacement.set_value(getattr(old, "value", "") or "no model")
    replacement.setToolTip(old.toolTip())
    replacement.setEnabled(old.isEnabled())
    replacement.clicked.connect(panel._open_model_menu)

    controls.removeWidget(old)
    old.hide()
    old.deleteLater()
    if index >= 0:
        controls.insertWidget(index, replacement, 0, Qt.AlignmentFlag.AlignVCenter)
    else:
        controls.addWidget(replacement, 0, Qt.AlignmentFlag.AlignVCenter)
    panel.model_button = replacement


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from app.desktop.composer import ComposerPanel

    original_init = ComposerPanel.__init__
    original_set_model = ComposerPanel.set_model
    original_stylesheet = theme.stylesheet

    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        _replace_model_button(self)

    def set_model(
        self: Any,
        model: str,
        *,
        history: Any = (),
        locked_reason: str = "",
    ) -> None:
        original_set_model(self, model, history=history, locked_reason=locked_reason)
        # Keep the button text pure. The disclosure chevron is painted natively,
        # so model names never contain a font-dependent glyph.
        self.model_button.setIcon(QIcon())
        self.model_button.setText(self.model_button.value)

    def stylesheet() -> str:
        return original_stylesheet() + _MODEL_SELECTOR_QSS

    ComposerPanel.__init__ = init
    ComposerPanel.set_model = set_model
    theme.stylesheet = stylesheet


__all__ = ["ModelSelectorButton", "install"]
