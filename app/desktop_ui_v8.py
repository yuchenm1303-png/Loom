from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from app import desktop_ui_v2 as v2
from app import desktop_ui_v7 as v7


DesktopEventBridge = v7.DesktopEventBridge
ComposerTextEdit = v7.ComposerTextEdit
ThreadListItemWidget = v7.ThreadListItemWidget


class LoomDesktopWindow(v7.LoomDesktopWindow):
    """Desktop v8: compatibility and destructive-action hardening for the library UI."""

    def _apply_thread_filter(self, value: str | None = None) -> None:
        super()._apply_thread_filter(value)
        # Older App Servers do not advertise threadManagement. Keep the legacy
        # section contract for those clients while the managed server uses the
        # clearer conversation-library label.
        if (
            self._thread_view == "active"
            and not self._thread_management_supported
            and self.thread_section_label.text().startswith("CHATS")
        ):
            self.thread_section_label.setText(
                self.thread_section_label.text().replace("CHATS", "THREADS", 1)
            )

    def _delete_selected_thread(self) -> None:
        record = self._selected_record()
        if record is None or not self._thread_management_supported:
            return
        title = v2._text(record.get("title")).strip() or "this conversation"

        message = QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle("Delete conversation")
        message.setText(f"Delete “{title}” permanently?")
        message.setInformativeText(
            "This removes Loom's conversation history and runtime records. "
            "Files in your external project workspace are not deleted."
        )
        delete_button = message.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = message.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        message.setDefaultButton(cancel_button)
        message.exec()
        if message.clickedButton() is not delete_button:
            return

        thread_id = v2._text(record.get("id"))
        self._run_rpc(
            f"thread-delete:{thread_id}",
            lambda: self._request_thread_action("thread/delete", {"threadId": thread_id}),
        )


__all__ = [
    "ComposerTextEdit",
    "DesktopEventBridge",
    "LoomDesktopWindow",
    "ThreadListItemWidget",
]
