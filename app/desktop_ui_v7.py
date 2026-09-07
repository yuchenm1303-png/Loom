from __future__ import annotations

from typing import Any

from app import desktop_ui_v6 as v6


DesktopEventBridge = v6.DesktopEventBridge
ComposerTextEdit = v6.ComposerTextEdit
ThreadListItemWidget = v6.ThreadListItemWidget


class LoomDesktopWindow(v6.LoomDesktopWindow):
    """Desktop v7: live message-flow reconciliation over the v6 visual layer."""

    def _capture_flow_notification(self, method: str, params: dict[str, Any]) -> bool:
        item = params.get("item") or {}
        item_id = v6.v2._text(item.get("id")) if isinstance(item, dict) else ""
        previous_item = dict(self._flow_live_items.get(item_id) or {}) if item_id else {}

        captured = super()._capture_flow_notification(method, params)
        if not captured:
            return False

        if method == "turn/started":
            turn = params.get("turn") or {}
            turn_id = v6.v2._text(turn.get("id")) if isinstance(turn, dict) else ""
            if turn_id:
                thread = self.current_snapshot.get("thread")
                if not isinstance(thread, dict):
                    thread = {}
                    self.current_snapshot["thread"] = thread
                thread["currentTurnId"] = turn_id

        if method == "item/completed" and item_id and previous_item:
            completed = dict(self._flow_live_items.get(item_id) or {})
            merged = previous_item
            merged.update(completed)
            self._flow_live_items[item_id] = merged

        return True


__all__ = [
    "ComposerTextEdit",
    "DesktopEventBridge",
    "LoomDesktopWindow",
    "ThreadListItemWidget",
]
