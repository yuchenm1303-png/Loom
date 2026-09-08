"""The message composer and the controls that sit under it.

Every control here is backed by something the App Server actually exposes:

- the workspace a conversation is bound to (``thread/start``);
- the permission mode it runs under (``thread/start`` + ``runtime.permissionModes``);
- the model the App Server process was launched with (``runtime.model``).

Reasoning effort is deliberately absent: the runtime registers a single model
role (``agent.fast``) with one binding, so there is nothing to choose between
and a control for it would only pretend.
"""

from __future__ import annotations

from typing import Any, Iterable

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from app.desktop import format as fmt
from app.desktop.widgets import repolish


MIN_HEIGHT = 40
MAX_HEIGHT = 220

PERMISSION_MODES: tuple[str, ...] = ("read-only", "approval", "workspace", "full-access")

# Wording taken from the resolved snapshots in app/agent_runtime/permissions.py
# rather than invented, so the menu cannot drift from what the runtime enforces.
PERMISSION_DETAIL: dict[str, str] = {
    "read-only": "Only read-only tools run. Nothing is written and nothing is asked.",
    "approval": "Read-only tools run freely. Editing files or running commands asks first.",
    "workspace": "Reads and workspace edits run freely. Sensitive actions still ask.",
    "full-access": "Every tool runs, including sensitive ones, without asking.",
}


class ComposerTextEdit(QTextEdit):
    """Enter sends, Shift+Enter inserts a newline."""

    sendRequested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.sendRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ControlButton(QPushButton):
    """A composer control: a quiet label that opens a menu or a dialog."""

    def __init__(
        self,
        *,
        icon: str = "",
        object_name: str = "composerControl",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._icon = icon
        self._value = ""

    def set_value(self, value: str, *, state: str = "") -> None:
        self._value = fmt.text(value)
        self.setText(f"{self._icon}  {self._value}" if self._icon else self._value)
        if self.property("mode") != state:
            self.setProperty("mode", state)
            repolish(self)

    @property
    def value(self) -> str:
        return self._value


def _menu_caption(text: str, parent: QMenu) -> QWidgetAction:
    """A non-interactive explanatory line inside a menu."""
    label = QLabel(text)
    label.setObjectName("menuCaption")
    label.setWordWrap(True)
    label.setContentsMargins(11, 4, 11, 7)
    label.setMaximumWidth(330)
    action = QWidgetAction(parent)
    action.setDefaultWidget(label)
    action.setEnabled(False)
    return action


class ComposerPanel(QFrame):
    """Prompt entry plus the decisions that apply to what is sent."""

    submitted = Signal(str)
    interrupted = Signal()
    workspaceRequested = Signal()
    permissionChosen = Signal(str)
    modelChosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("composerFrame")
        self.setProperty("focused", False)
        self._permission_modes: tuple[str, ...] = PERMISSION_MODES
        self._thread_permission = ""
        self._pending_permission = ""
        self._model_history: list[str] = []
        self._model_locked_reason = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 10, 9)
        layout.setSpacing(8)

        self.editor = ComposerTextEdit()
        self.editor.setObjectName("composer")
        self.editor.setPlaceholderText("Message Loom…")
        self.editor.setMinimumHeight(MIN_HEIGHT)
        self.editor.setMaximumHeight(MAX_HEIGHT)
        self.editor.setFrameShape(QFrame.Shape.NoFrame)
        self.editor.sendRequested.connect(self._submit)
        self.editor.textChanged.connect(self.sync_height)
        self.editor.installEventFilter(self)
        layout.addWidget(self.editor)

        bar = QHBoxLayout()
        bar.setSpacing(6)

        self.workspace_button = ControlButton(icon="◧")
        self.workspace_button.setToolTip("Change the project this conversation works in")
        self.workspace_button.clicked.connect(self.workspaceRequested.emit)
        bar.addWidget(self.workspace_button)

        self.permission_button = ControlButton(icon="⛊")
        self.permission_button.clicked.connect(self._open_permission_menu)
        bar.addWidget(self.permission_button)

        self.model_button = ControlButton(icon="◈")
        self.model_button.clicked.connect(self._open_model_menu)
        bar.addWidget(self.model_button)

        bar.addStretch(1)

        self.usage_label = QLabel("")
        self.usage_label.setObjectName("composerHint")
        self.usage_label.hide()
        bar.addWidget(self.usage_label)

        self.state_label = QLabel("")
        self.state_label.setObjectName("composerState")
        self.state_label.hide()
        bar.addWidget(self.state_label)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_button.clicked.connect(self.interrupted.emit)
        self.stop_button.hide()
        bar.addWidget(self.stop_button)

        self.send_button = QPushButton("↑")
        self.send_button.setObjectName("sendButton")
        self.send_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_button.setToolTip("Send  ·  Enter")
        self.send_button.setFixedSize(34, 34)
        self.send_button.clicked.connect(self._submit)
        bar.addWidget(self.send_button)
        layout.addLayout(bar)

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.sync_height()

    # ---- text ------------------------------------------------------------

    def text(self) -> str:
        return self.editor.toPlainText().strip()

    def clear(self) -> None:
        self.editor.clear()

    def set_text(self, value: str) -> None:
        self.editor.setPlainText(value)
        self.editor.setFocus()
        cursor = self.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)

    def sync_height(self) -> None:
        document = self.editor.document()
        height = document.size().height() + document.documentMargin() * 2 + 10
        self.editor.setFixedHeight(int(max(float(MIN_HEIGHT), min(height, float(MAX_HEIGHT)))))

    def _submit(self) -> None:
        value = self.text()
        if value and self.send_button.isEnabled():
            self.submitted.emit(value)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is self.editor:
            if event.type() == QEvent.Type.FocusIn:
                self.setProperty("focused", True)
                repolish(self)
            elif event.type() == QEvent.Type.FocusOut:
                self.setProperty("focused", False)
                repolish(self)
        return super().eventFilter(watched, event)

    # ---- state -----------------------------------------------------------

    def set_workspace(self, workspace: str) -> None:
        workspace = fmt.text(workspace)
        self.workspace_button.set_value(fmt.short_path(workspace) or "No project")
        self.workspace_button.setToolTip(workspace or "No workspace selected")

    def set_permission_modes(self, modes: Iterable[Any]) -> None:
        """Adopt the modes the connected runtime reports as valid."""
        resolved = tuple(
            mode for mode in (fmt.text(value).strip() for value in modes or ()) if mode
        )
        self._permission_modes = resolved or PERMISSION_MODES

    def set_permission(self, mode: str, *, thread_mode: str = "") -> None:
        """Show the mode that governs the next message.

        A durable Thread's mode is fixed when it is created, so an open
        conversation shows its own mode and a draft shows the pending default.
        """
        self._pending_permission = fmt.text(mode) or self._pending_permission
        self._thread_permission = fmt.text(thread_mode)
        shown = self._thread_permission or self._pending_permission
        self.permission_button.set_value(shown, state=shown)
        detail = PERMISSION_DETAIL.get(shown, "")
        if self._thread_permission and self._pending_permission != self._thread_permission:
            detail += (
                f"\n\nThis conversation is fixed at {self._thread_permission}. "
                f"New conversations will use {self._pending_permission}."
            )
        self.permission_button.setToolTip(detail or "Permission mode")

    def set_model(self, model: str, *, history: Iterable[str] = (), locked_reason: str = "") -> None:
        model = fmt.text(model)
        self._model_history = [
            value
            for value in dict.fromkeys(fmt.text(item).strip() for item in history)
            if value and value != model
        ]
        self._model_locked_reason = fmt.text(locked_reason)
        self.model_button.set_value(model or "no model")
        self.model_button.setToolTip(
            self._model_locked_reason
            or "Model this App Server was launched with. Changing it restarts the local server."
        )
        self.model_button.setEnabled(not self._model_locked_reason)

    def set_usage(self, total: int) -> None:
        self.usage_label.setText(f"{total:,} tokens" if total else "")
        self.usage_label.setVisible(bool(total))

    def set_state(self, text: str) -> None:
        self.state_label.setText(text)
        self.state_label.setVisible(bool(text))

    def set_busy(self, *, active: bool, can_send: bool, read_only: bool) -> None:
        self.stop_button.setVisible(active)
        self.stop_button.setEnabled(active)
        self.send_button.setEnabled(can_send)
        self.editor.setReadOnly(read_only)

    def set_placeholder(self, text: str) -> None:
        self.editor.setPlaceholderText(text)

    # ---- menus -----------------------------------------------------------

    def _open_permission_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(_menu_caption("What Loom may do without asking", menu))
        current = self._thread_permission or self._pending_permission
        actions: dict[Any, str] = {}
        for mode in self._permission_modes:
            action = menu.addAction(mode)
            action.setCheckable(True)
            action.setChecked(mode == current)
            action.setToolTip(PERMISSION_DETAIL.get(mode, ""))
            actions[action] = mode
        if self._thread_permission:
            menu.addSeparator()
            menu.addAction(
                _menu_caption(
                    "A conversation keeps the mode it was created with, so a "
                    "change here applies to the next one.",
                    menu,
                )
            )
        chosen = menu.exec(self.mapToGlobal(self.permission_button.geometry().topLeft()))
        mode = actions.get(chosen)
        if mode and mode != current:
            self.permissionChosen.emit(mode)

    def _open_model_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction(
            _menu_caption(
                "The local App Server runs one model. Switching restarts it; "
                "durable conversations are kept.",
                menu,
            )
        )
        current = self.model_button.value
        actions: dict[Any, str] = {}
        for model in (current, *self._model_history):
            if not model:
                continue
            action = menu.addAction(model)
            action.setCheckable(True)
            action.setChecked(model == current)
            actions[action] = model
        menu.addSeparator()
        custom = menu.addAction("Other model…")

        chosen = menu.exec(self.mapToGlobal(self.model_button.geometry().topLeft()))
        if chosen is None:
            return
        if chosen is custom:
            value, accepted = QInputDialog.getText(
                self,
                "Switch model",
                "Model name for the local App Server",
                QLineEdit.EchoMode.Normal,
                current,
            )
            value = " ".join(value.split()) if accepted else ""
            if value and value != current:
                self.modelChosen.emit(value)
            return
        model = actions.get(chosen)
        if model and model != current:
            self.modelChosen.emit(model)


__all__ = [
    "MAX_HEIGHT",
    "MIN_HEIGHT",
    "PERMISSION_DETAIL",
    "PERMISSION_MODES",
    "ComposerPanel",
    "ComposerTextEdit",
    "ControlButton",
]
