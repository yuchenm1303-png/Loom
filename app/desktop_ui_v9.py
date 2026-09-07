from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote, unquote

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QTextCursor

from app import desktop_ui_v2 as v2
from app import desktop_ui_v8 as v8
from app.desktop_message_flow import FlowItem, FlowTurn, build_message_flow


DesktopEventBridge = v8.DesktopEventBridge
ComposerTextEdit = v8.ComposerTextEdit
ThreadListItemWidget = v8.ThreadListItemWidget

_FLOW_ACTIVE_STATUSES = {
    "running",
    "starting",
    "streaming",
    "waiting",
    "waiting_approval",
}


class LoomDesktopWindow(v8.LoomDesktopWindow):
    """Desktop v9: Codex-style turn activity interleaved with durable chat."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._flow_live_items: dict[str, dict[str, Any]] = {}
        self._flow_live_turns: dict[str, dict[str, Any]] = {}
        self._flow_expanded_items: set[str] = set()
        self._flow_collapsed_turns: set[str] = set()
        self._flow_entry_count = 0
        self._flow_timer: QTimer | None = None
        super().__init__(*args, **kwargs)
        self._flow_timer = QTimer(self)
        self._flow_timer.setInterval(1000)
        self._flow_timer.timeout.connect(self._tick_flow_clock)
        self._flow_timer.start()

    def _build_ui(self) -> None:
        super()._build_ui()
        self.transcript.setOpenExternalLinks(False)
        self.transcript.anchorClicked.connect(self._on_flow_anchor)

    def _apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        durable_ids: set[str] = set()
        durable_turn_ids: set[str] = set()
        for turn in snapshot.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            turn_id = v2._text(turn.get("id"))
            if turn_id:
                durable_turn_ids.add(turn_id)
            for item in turn.get("items") or []:
                if not isinstance(item, dict):
                    continue
                item_id = v2._text(item.get("id"))
                if item_id:
                    durable_ids.add(item_id)

        self._flow_live_items = {
            item_id: item
            for item_id, item in self._flow_live_items.items()
            if item_id not in durable_ids
        }
        for turn_id in durable_turn_ids:
            self._flow_live_turns.pop(turn_id, None)
        super()._apply_snapshot(snapshot)

    def _capture_flow_notification(self, method: str, params: dict[str, Any]) -> bool:
        thread_id = v2._text(params.get("threadId"))
        item = params.get("item") or {}
        if not thread_id and isinstance(item, dict):
            thread_id = v2._text(item.get("threadId"))
        if self.current_thread_id and thread_id and thread_id != self.current_thread_id:
            return False

        if method == "turn/started":
            turn = params.get("turn") or {}
            if isinstance(turn, dict):
                turn_id = v2._text(turn.get("id"))
                if turn_id:
                    self._flow_live_turns[turn_id] = dict(turn)
                    thread = self.current_snapshot.get("thread")
                    if not isinstance(thread, dict):
                        thread = {}
                        self.current_snapshot["thread"] = thread
                    thread["currentTurnId"] = turn_id

        elif method == "turn/completed":
            turn = params.get("turn") or {}
            if isinstance(turn, dict):
                turn_id = v2._text(turn.get("id"))
                if turn_id:
                    previous = dict(self._flow_live_turns.get(turn_id) or {})
                    previous.update(turn)
                    self._flow_live_turns[turn_id] = previous

        elif method == "item/started" and isinstance(item, dict):
            item_id = v2._text(item.get("id"))
            if item_id and v2._text(item.get("type")) not in {
                "assistant_message",
                "user_message",
            }:
                self._flow_live_items[item_id] = dict(item)

        elif method == "item/delta":
            item_id = v2._text(params.get("itemId"))
            delta = params.get("delta") or {}
            if item_id and isinstance(delta, dict) and item_id in self._flow_live_items:
                current = dict(self._flow_live_items[item_id])
                if delta.get("status"):
                    current["status"] = delta.get("status")
                for key in ("stdout", "stderr"):
                    if delta.get(key):
                        current[key] = v2._text(current.get(key)) + v2._text(delta.get(key))
                self._flow_live_items[item_id] = current

        elif method == "item/completed" and isinstance(item, dict):
            item_id = v2._text(item.get("id"))
            if item_id:
                # Completion notifications are intentionally sparse. Merge them
                # into the start/delta record so argv, arguments, cwd, and output
                # do not disappear while the durable snapshot is reconciling.
                current = dict(self._flow_live_items.get(item_id) or {})
                current.update(item)
                self._flow_live_items[item_id] = current

        elif method == "approval/requested":
            approval = params.get("approval") or {}
            if isinstance(approval, dict):
                call_id = v2._text(approval.get("callId"))
                if call_id:
                    item_id = v2._text(approval.get("itemId")) or f"approval:{call_id}"
                    current = dict(self._flow_live_items.get(item_id) or {})
                    current.update(
                        {
                            "id": item_id,
                            "threadId": thread_id or self.current_thread_id,
                            "turnId": v2._text(params.get("turnId")) or self.current_turn_id,
                            "type": "approval",
                            "status": "waiting",
                            "toolName": approval.get("toolName"),
                            "arguments": approval.get("arguments") or {},
                            "effect": approval.get("effect"),
                            "reason": approval.get("reason"),
                        }
                    )
                    self._flow_live_items[item_id] = current

        return True

    def _on_notification(self, method: str, params: Any) -> None:
        captured = isinstance(params, dict) and self._capture_flow_notification(method, params)
        super()._on_notification(method, params)
        if captured and method in {
            "turn/started",
            "turn/completed",
            "item/started",
            "item/delta",
            "item/completed",
            "approval/requested",
        }:
            self._render_transcript()

    def _tick_flow_clock(self) -> None:
        active = any(
            v2._text(turn.get("status")).casefold() in _FLOW_ACTIVE_STATUSES
            for turn in self._flow_live_turns.values()
        )
        if not active:
            thread = self.current_snapshot.get("thread") or {}
            if isinstance(thread, dict):
                active = v2._text(thread.get("status")).casefold() in _FLOW_ACTIVE_STATUSES
        if active and self.transcript.isVisible():
            self._render_transcript()

    def _on_flow_anchor(self, url: QUrl) -> None:
        if url.scheme() != "loom":
            return
        target = unquote(url.path().lstrip("/"))
        if not target:
            return
        if url.host() == "item":
            if target in self._flow_expanded_items:
                self._flow_expanded_items.remove(target)
            else:
                self._flow_expanded_items.add(target)
        elif url.host() == "turn":
            if target in self._flow_collapsed_turns:
                self._flow_collapsed_turns.remove(target)
            else:
                self._flow_collapsed_turns.add(target)
        else:
            return
        self._render_transcript()

    def _render_transcript(self) -> None:
        if not hasattr(self, "transcript"):
            return
        was_empty = self.empty_state.isVisible() if hasattr(self, "empty_state") else False
        was_transcript = self.transcript.isVisible()
        flow = build_message_flow(
            self.current_snapshot,
            live_items=self._flow_live_items.values(),
            live_turns=self._flow_live_turns,
            live_assistant=self._live_assistant,
            optimistic_user=self._optimistic_user,
        )

        rows: list[str] = []
        count = 0
        for turn in flow:
            rows.extend(self._turn_flow_rows(turn))
            count += len(turn.items)

        previous = self._flow_entry_count
        if not rows:
            self.transcript.hide()
            self.empty_state.show()
        else:
            self.empty_state.hide()
            self.transcript.show()
            bar = self.transcript.verticalScrollBar()
            follow = bar.maximum() - bar.value() <= 64
            old = bar.value()
            self.transcript.setHtml(self._flow_document("".join(rows)))
            if follow:
                cursor = self.transcript.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                self.transcript.setTextCursor(cursor)
            else:
                bar.setValue(min(old, bar.maximum()))

        self._flow_entry_count = count
        self._last_transcript_card_count = count
        if self.empty_state.isVisible() and not was_empty:
            self._fade_in_widget(self.empty_state, "empty-state", start=0.12, duration=230)
        if self.transcript.isVisible() and not was_transcript:
            self._fade_in_widget(self.transcript, "transcript", start=0.20, duration=175)
        elif self.transcript.isVisible() and count > previous:
            self._pulse_widget(self.transcript, "new-message", start=0.94, duration=95)

    def _turn_flow_rows(self, turn: FlowTurn) -> list[str]:
        if turn.source == "system":
            return [self._flow_item_card(item) for item in turn.items]

        turn_id = quote(turn.turn_id, safe="")
        collapsed = turn.turn_id in self._flow_collapsed_turns
        chevron = "›" if collapsed else "⌄"
        status = html.escape(turn.status.replace("_", " ").title())
        header = (
            "<div class='turnHeader'>"
            f"<a href='loom://turn/{turn_id}'>"
            f"<span class='elapsed'>{html.escape(turn.elapsed())}</span> "
            f"<span class='turnState'>{status}</span> "
            f"<span class='chevron'>{chevron}</span></a>"
            "</div>"
        )
        if collapsed:
            return [header]
        return [header, *(self._flow_item_card(item) for item in turn.items)]

    def _flow_item_card(self, item: FlowItem) -> str:
        if item.kind in {"user", "assistant"}:
            if not item.text:
                return ""
            return self._message_card(item.kind, item.text, streaming=item.streaming)

        expanded = item.item_id in self._flow_expanded_items
        escaped_id = quote(item.item_id, safe="")
        marker = html.escape(item.marker)
        title = html.escape(item.title)
        detail = html.escape(item.detail)
        chevron = "⌄" if expanded else "›"
        status = item.status.replace("_", " ").title()
        status_html = (
            f"<span class='activityStatus'>{html.escape(status)}</span>"
            if item.status in _FLOW_ACTIVE_STATUSES or item.tone in {"bad", "warn"}
            else ""
        )
        if item.expandable:
            lead = (
                f"<a href='loom://item/{escaped_id}'>"
                f"<span class='activityMarker {item.tone}'>{marker}</span>"
                f"<span class='activityTitle'>{title}</span>{status_html}"
                f"<span class='activityChevron'>{chevron}</span></a>"
            )
        else:
            lead = (
                f"<span class='activityMarker {item.tone}'>{marker}</span>"
                f"<span class='activityTitle'>{title}</span>{status_html}"
            )
        detail_html = f"<div class='activityDetail'>{detail}</div>" if detail else ""
        body_html = ""
        if expanded and item.body:
            body_html = f"<pre class='activityBody'>{html.escape(item.body)}</pre>"
        return f"<div class='activityRow'>{lead}{detail_html}{body_html}</div>"

    @staticmethod
    def _flow_document(content: str) -> str:
        return """
        <style>
        body{color:#dfe3e9;font-family:'Segoe UI',sans-serif;font-size:14px;margin:10px 10px 42px;background:#090a0e}
        a{color:inherit;text-decoration:none}
        .turnHeader{border-bottom:1px solid #20242b;padding:15px 0 9px;margin:0 0 9px;color:#818997;font-size:11px}
        .elapsed{color:#969da9;font-weight:650}.turnState{color:#5f6774;font-size:9px;margin-left:8px}.chevron{color:#626a76;margin-left:5px}
        .message{margin:0 0 24px}.meta{color:#6d7586;font-size:9px;font-weight:700;letter-spacing:1.15px;margin:0 0 7px 1px}.assistant .meta{color:#8d85ee}.stream{color:#aaa4f6;font-size:8px}.body{line-height:1.52}.user .body{background:#11141b;border:1px solid #252a35;border-radius:12px;padding:10px 13px;margin-left:58px}
        .activityRow{margin:0 0 8px;padding:3px 0 3px 2px;color:#aab0ba;font-size:12px}.activityMarker{display:inline-block;width:25px;font-family:'Cascadia Mono','Consolas',monospace;font-weight:700}.activityMarker.good{color:#69b795}.activityMarker.active{color:#8f87ed}.activityMarker.bad{color:#dc858f}.activityMarker.warn{color:#daa85f}.activityMarker.muted{color:#69717d}.activityTitle{color:#aeb4bd}.activityStatus{color:#6e7683;font-size:9px;margin-left:8px}.activityChevron{color:#5b626e;margin-left:7px}.activityDetail{color:#68717e;font-family:'Cascadia Mono','Consolas',monospace;font-size:10px;margin:3px 0 0 25px;white-space:pre-wrap}.activityBody{color:#aeb5c0;background:#0c0f14;border:1px solid #1b2028;border-radius:8px;font-family:'Cascadia Mono','Consolas',monospace;font-size:10px;margin:7px 0 4px 24px;padding:9px 10px;white-space:pre-wrap}
        p{margin:0 0 10px}strong{color:#f4f5f8}code{background:#151923;color:#d5d0ff;font-family:'Cascadia Mono','Consolas',monospace;font-size:12px}.h1,.h2,.h3{color:#f3f4f7;font-weight:700;margin:13px 0 8px}.h1{font-size:18px}.h2{font-size:16px}.h3{font-size:14px}ul,ol{margin:4px 0 11px 18px}.check{font-weight:800}.check.done{color:#78c3a2}.check.todo{color:#727b8b}blockquote{color:#abb1bd;border-left:2px solid #393650;margin:8px 0 11px;padding:3px 0 3px 11px}.codeBlock{background:#0c0f14;border:1px solid #202631;border-radius:9px;margin:8px 0 12px}.codeLabel{color:#666f81;font-family:'Consolas',monospace;font-size:9px;padding:7px 10px 0}pre{color:#c7ccd6;background:transparent;font-family:'Cascadia Mono','Consolas',monospace;font-size:11px;margin:0;padding:9px 10px 10px;white-space:pre-wrap}
        </style>
        """ + content

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        if self._flow_timer is not None:
            self._flow_timer.stop()
        super().closeEvent(event)


__all__ = [
    "ComposerTextEdit",
    "DesktopEventBridge",
    "LoomDesktopWindow",
    "ThreadListItemWidget",
]
