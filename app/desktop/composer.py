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

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QFontMetrics, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from app.ai.model_store import ModelConfigStore, StoredModel, model_id_from_selection
from app.desktop import format as fmt
from app.desktop.widgets import repolish
from app.desktop.interaction import FocusFrame


MIN_HEIGHT = 40
MAX_HEIGHT = 220
MODEL_MENU_WIDTH = 258
MODEL_MENU_EDGE_GAP = 10
MODEL_MENU_TEXT_WIDTH = 196

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


def _menu_caption(
    text: str,
    parent: QMenu,
    *,
    max_width: int = 330,
    compact: bool = False,
) -> QWidgetAction:
    """A non-interactive explanatory line inside a menu."""
    label = QLabel(text)
    label.setObjectName("menuCaption")
    label.setWordWrap(True)
    if compact:
        label.setContentsMargins(9, 3, 9, 5)
    else:
        label.setContentsMargins(11, 4, 11, 7)
    label.setMaximumWidth(max_width)
    action = QWidgetAction(parent)
    action.setDefaultWidget(label)
    action.setEnabled(False)
    return action


def _elide_menu_text(menu: QMenu, text: str, *, width: int = MODEL_MENU_TEXT_WIDTH) -> str:
    """Keep one long provider/model label from widening the whole popup."""
    return QFontMetrics(menu.font()).elidedText(
        fmt.text(text), Qt.TextElideMode.ElideRight, max(80, int(width))
    )


def _bounded_menu_position(
    menu: QMenu,
    button: QWidget,
    *,
    width: int = MODEL_MENU_WIDTH,
) -> QPoint:
    """Size and position a popup so it stays inside the owning app window.

    Qt normally keeps menus on-screen, but that can still let a composer popup
    extend outside a small Loom window. The model picker is deliberately a
    compact in-window surface, preferring the space above the composer control.
    """
    window = button.window()
    gap = MODEL_MENU_EDGE_GAP
    menu.setFixedWidth(width)
    menu.ensurePolished()

    max_height = max(140, window.height() - gap * 2)
    menu.setMaximumHeight(max_height)
    menu_height = min(menu.sizeHint().height(), max_height)

    window_top_left = window.mapToGlobal(QPoint(0, 0))
    left = window_top_left.x() + gap
    top = window_top_left.y() + gap
    right = window_top_left.x() + window.width() - gap
    bottom = window_top_left.y() + window.height() - gap

    button_top = button.mapToGlobal(QPoint(0, 0))
    button_bottom = button.mapToGlobal(QPoint(0, button.height()))

    max_x = max(left, right - width)
    x = min(max(button_top.x(), left), max_x)

    above = button_top.y() - menu_height - 6
    below = button_bottom.y() + 6
    if above >= top:
        y = above
    elif below + menu_height <= bottom:
        y = below
    else:
        y = min(max(above, top), max(top, bottom - menu_height))
    return QPoint(x, y)


class AddModelDialog(QDialog):
    """Small setup dialog for an OpenAI or OpenAI-compatible Agent model."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add model API")
        self.setModal(True)
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(12)

        hint = QLabel(
            "Connect an OpenAI-compatible endpoint or OpenAI directly. "
            "For Agent use, the selected model should support tool calling and streaming."
        )
        hint.setWordWrap(True)
        hint.setObjectName("menuCaption")
        layout.addWidget(hint)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Work API · Claude")
        form.addRow("Name", self.name_edit)

        self.adapter_combo = QComboBox()
        self.adapter_combo.addItem("OpenAI-compatible", "openai-compatible")
        self.adapter_combo.addItem("OpenAI", "openai")
        self.adapter_combo.currentIndexChanged.connect(self._sync_adapter)
        form.addRow("API type", self.adapter_combo)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.example.com/v1")
        form.addRow("Base URL", self.base_url_edit)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("Model ID exposed by the API")
        form.addRow("Model", self.model_edit)

        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("API key (stored in the OS credential store)")
        form.addRow("API key", self.api_key_edit)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_button is not None:
            save_button.setText("Add model")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._sync_adapter()

    def _sync_adapter(self) -> None:
        compatible = self.adapter() == "openai-compatible"
        self.base_url_edit.setEnabled(compatible)
        self.base_url_edit.setPlaceholderText(
            "https://api.example.com/v1" if compatible else "OpenAI default endpoint"
        )
        if not compatible:
            self.base_url_edit.clear()

    def adapter(self) -> str:
        return str(self.adapter_combo.currentData() or "openai-compatible")

    def values(self) -> dict[str, str]:
        return {
            "display_name": " ".join(self.name_edit.text().split()),
            "adapter": self.adapter(),
            "base_url": self.base_url_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "api_key": self.api_key_edit.text().strip(),
        }

    def accept(self) -> None:
        values = self.values()
        missing = []
        if not values["display_name"]:
            missing.append("Name")
        if values["adapter"] == "openai-compatible" and not values["base_url"]:
            missing.append("Base URL")
        if not values["model"]:
            missing.append("Model")
        if not values["api_key"]:
            missing.append("API key")
        if missing:
            QMessageBox.warning(self, "Missing model settings", "Please fill in: " + ", ".join(missing))
            return
        super().accept()


class ComposerPanel(FocusFrame):
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
                self.animate_focus(True)
                repolish(self)
            elif event.type() == QEvent.Type.FocusOut:
                self.setProperty("focused", False)
                self.animate_focus(False)
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
            if value and value != model and model_id_from_selection(value) is None
        ]
        self._model_locked_reason = fmt.text(locked_reason)
        self.model_button.set_value(model or "no model")
        self.model_button.setToolTip(
            self._model_locked_reason
            or "Switch the model or a saved API connection. Changing it restarts the local App Server."
        )
        self.model_button.setEnabled(not self._model_locked_reason)

    def set_usage(self, total: int) -> None:
        self.usage_label.setText(f"{total:,} tokens" if total else "")
        self.usage_label.setVisible(bool(total))

    def set_state(self, text: str, *, tone: str = "") -> None:
        """What the conversation is doing right now, and how it should read.

        The tone is the difference between "still working", "your turn to
        answer" and "this failed"; without it every state is the same grey.
        """
        self.state_label.setText(text)
        self.state_label.setVisible(bool(text))
        if self.state_label.property("tone") != tone:
            self.state_label.setProperty("tone", tone)
            repolish(self.state_label)

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

    def _stored_models(self) -> tuple[tuple[StoredModel, ...], str | None, str]:
        try:
            store = ModelConfigStore()
            return store.list_models(), store.active_model_id, ""
        except Exception as exc:
            return (), None, str(exc)

    def _add_model(self) -> StoredModel | None:
        dialog = AddModelDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        try:
            return ModelConfigStore().save_model(**dialog.values())
        except Exception as exc:
            QMessageBox.warning(self, "Could not save model", str(exc))
            return None

    def _open_model_menu(self) -> None:
        menu = QMenu(self)
        menu.setObjectName("modelMenu")
        menu.setToolTipsVisible(True)
        menu.addAction(
            _menu_caption(
                "Switching restarts the local model server. Conversations stay open.",
                menu,
                max_width=232,
                compact=True,
            )
        )
        current = self.model_button.value
        actions: dict[Any, str] = {}
        saved, active_model_id, load_error = self._stored_models()

        if saved:
            for entry in saved:
                full_label = f"{entry.display_name}  ·  {entry.model}"
                action = menu.addAction(_elide_menu_text(menu, full_label))
                action.setCheckable(True)
                action.setChecked(entry.model_id == active_model_id and entry.model == current)
                endpoint = entry.base_url or "OpenAI default endpoint"
                action.setToolTip(
                    f"{entry.display_name}\n{entry.adapter.value}\n{endpoint}\n{entry.model}"
                )
                actions[action] = entry.selection
            menu.addSeparator()
        elif load_error:
            menu.addAction(
                _menu_caption(
                    f"Saved models unavailable: {load_error}",
                    menu,
                    max_width=232,
                    compact=True,
                )
            )

        # Preserve the existing lightweight model-name switch for one provider.
        # A saved connection is a different lane because it also changes endpoint/key.
        if active_model_id is None:
            recent = [current, *self._model_history]
        else:
            recent = list(self._model_history)
        for model in dict.fromkeys(recent):
            if not model or model_id_from_selection(model) is not None:
                continue
            action = menu.addAction(_elide_menu_text(menu, model))
            action.setCheckable(True)
            action.setChecked(active_model_id is None and model == current)
            if action.text() != model:
                action.setToolTip(model)
            actions[action] = model

        menu.addSeparator()
        add_api = menu.addAction("Add API / model…")
        custom = menu.addAction("Other model…")

        chosen = menu.exec(_bounded_menu_position(menu, self.model_button))
        if chosen is None:
            return
        if chosen is add_api:
            entry = self._add_model()
            if entry is not None:
                self.modelChosen.emit(entry.selection)
            return
        if chosen is custom:
            value, accepted = QInputDialog.getText(
                self,
                "Switch model",
                "Model name for the current API connection",
                QLineEdit.EchoMode.Normal,
                current,
            )
            value = " ".join(value.split()) if accepted else ""
            if value and value != current:
                self.modelChosen.emit(value)
            return
        model = actions.get(chosen)
        if model:
            self.modelChosen.emit(model)


__all__ = [
    "MAX_HEIGHT",
    "MIN_HEIGHT",
    "MODEL_MENU_EDGE_GAP",
    "MODEL_MENU_WIDTH",
    "PERMISSION_DETAIL",
    "PERMISSION_MODES",
    "AddModelDialog",
    "ComposerPanel",
    "ComposerTextEdit",
    "ControlButton",
]
