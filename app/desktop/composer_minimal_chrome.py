"""Final minimal chrome for the composer control row.

The composer already has separate modules for behaviour, menus, motion and icon
rendering. This pass only establishes the final visual hierarchy after those
modules are installed: ordinary controls behave like a toolbar, permission is
semantic typography, the model picker is a quiet selector, usage is metadata,
and Send is the only filled action.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel, QPushButton

from app.desktop import theme


_INSTALLED = False

_COMPOSER_MINIMAL_QSS = r"""
/* One restrained editor surface. Avoid gradients and nested pill surfaces. */
QFrame#composerFrame,
QFrame#composerFrame:hover {
    background:#1b1c21;
    border:1px solid #30323a;
    border-radius:16px;
}
QFrame#composerFrame[focused="true"] {
    background:#1c1d22;
    border-color:#454751;
}
QTextEdit#composer {
    background:transparent;
    border:none;
    color:#eceef2;
    padding:4px 3px 5px 3px;
}

/* Attach / workspace / permission are toolbar actions, not pills. */
QPushButton#composerControl,
QPushButton#composerAttach,
QPushButton#composerWorkspace,
QPushButton#composerPermission {
    min-height:29px;
    max-height:29px;
    padding:0 7px;
    margin:0;
    background:transparent;
    border:1px solid transparent;
    border-radius:6px;
    color:#aeb3bd;
    font-size:11px;
    font-weight:580;
}
QPushButton#composerControl:hover,
QPushButton#composerAttach:hover,
QPushButton#composerWorkspace:hover,
QPushButton#composerPermission:hover {
    background:#24252b;
    border-color:transparent;
    color:#eef0f3;
}
QPushButton#composerControl:pressed,
QPushButton#composerAttach:pressed,
QPushButton#composerWorkspace:pressed,
QPushButton#composerPermission:pressed {
    background:#202126;
    border-color:transparent;
}
QPushButton#composerControl:disabled,
QPushButton#composerAttach:disabled,
QPushButton#composerWorkspace:disabled,
QPushButton#composerPermission:disabled {
    background:transparent;
    border-color:transparent;
    color:#555a64;
}

/* Permission communicates risk through colour only; never through another box. */
QPushButton#composerPermission[mode="full-access"] {
    background:transparent;
    border-color:transparent;
    color:#e0b86f;
}
QPushButton#composerPermission[mode="full-access"]:hover {
    background:#28231a;
    border-color:transparent;
    color:#efc77e;
}
QPushButton#composerPermission[mode="read-only"] {
    background:transparent;
    border-color:transparent;
    color:#91afc7;
}
QPushButton#composerPermission[mode="workspace"] {
    background:transparent;
    border-color:transparent;
    color:#aaa5d2;
}
QPushButton#composerPermission[mode="approval"] {
    background:transparent;
    border-color:transparent;
    color:#b4b0ba;
}

/* Model is still a selector, but no longer a grey plastic capsule. */
QPushButton#composerModel {
    min-height:29px;
    max-height:29px;
    padding:0 24px 0 8px;
    margin:0;
    background:transparent;
    border:1px solid transparent;
    border-radius:6px;
    color:#e5e7eb;
    font-size:11px;
    font-weight:590;
    text-align:left;
}
QPushButton#composerModel:hover {
    background:#24252b;
    border-color:transparent;
    color:#ffffff;
}
QPushButton#composerModel:pressed {
    background:#202126;
    border-color:transparent;
}
QPushButton#composerModel:disabled {
    background:transparent;
    border-color:transparent;
    color:#676c75;
}

/* Token usage is information, not a button or badge. */
QLabel#composerUsage,
QLabel#composerUsage:hover {
    min-height:25px;
    max-height:25px;
    padding:0 4px;
    margin:0;
    background:transparent;
    border:none;
    border-radius:0;
    color:#7e8590;
    font-size:10px;
    font-weight:560;
}

/* Send is deliberately the only filled control in the row. */
QPushButton#sendButton {
    min-width:36px;
    max-width:36px;
    min-height:36px;
    max-height:36px;
    padding:0;
    margin:0;
    background:#765ee8;
    border:none;
    border-radius:18px;
    color:#ffffff;
}
QPushButton#sendButton:hover {
    background:#836cf0;
    border:none;
}
QPushButton#sendButton:pressed {
    background:#6b53dc;
    border:none;
}
QPushButton#sendButton:disabled {
    background:#292a30;
    border:none;
    color:#606670;
}

QPushButton#stopButton {
    min-height:29px;
    max-height:29px;
    padding:0 8px;
    margin:0;
    background:transparent;
    border:1px solid transparent;
    border-radius:6px;
    color:#cf858f;
}
QPushButton#stopButton:hover {
    background:#281d20;
    border-color:transparent;
    color:#e9a0aa;
}
"""


def _remove_usage_ring() -> None:
    """The usage value should paint exactly like metadata, without badge iconography."""
    from app.desktop import composer_polish

    cls = composer_polish.UsageBadge
    if getattr(cls, "_loom_minimal_usage_installed", False):
        return
    cls._loom_minimal_usage_installed = True

    def paint_event(self: Any, event: Any) -> None:
        QLabel.paintEvent(self, event)

    cls.paintEvent = paint_event


def _install_geometry() -> None:
    """Undo earlier fixed pill dimensions so the final toolbar QSS is authoritative."""
    from app.desktop.composer import ComposerPanel

    if getattr(ComposerPanel, "_loom_minimal_chrome_installed", False):
        return
    ComposerPanel._loom_minimal_chrome_installed = True
    original_init = ComposerPanel.__init__

    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)

        for button in (
            getattr(self, "workspace_button", None),
            getattr(self, "permission_button", None),
            getattr(self, "model_button", None),
        ):
            if button is not None:
                button.setMinimumHeight(29)
                button.setMaximumHeight(29)

        # Attachment support is optional and may be installed by another layer.
        for button in self.findChildren(QPushButton):
            if button.objectName() == "composerAttach":
                button.setMinimumHeight(29)
                button.setMaximumHeight(29)

        usage = getattr(self, "usage_label", None)
        if usage is not None:
            usage.setMinimumHeight(25)
            usage.setMaximumHeight(25)

        send = getattr(self, "send_button", None)
        if send is not None:
            send.setFixedSize(36, 36)

        outer = self.layout()
        if outer is not None:
            outer.setContentsMargins(14, 10, 10, 9)
            outer.setSpacing(7)
            if outer.count() > 1:
                controls = outer.itemAt(1).layout()
                if controls is not None:
                    controls.setContentsMargins(0, 0, 0, 0)
                    controls.setSpacing(3)
            outer.invalidate()
            outer.activate()
            self.updateGeometry()

    ComposerPanel.__init__ = init


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    _remove_usage_ring()
    _install_geometry()

    original_stylesheet = theme.stylesheet

    def stylesheet() -> str:
        return original_stylesheet() + _COMPOSER_MINIMAL_QSS

    theme.stylesheet = stylesheet


__all__ = ["install"]
