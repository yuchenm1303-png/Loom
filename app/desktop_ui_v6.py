from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote, unquote

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QFrame, QLabel, QPushButton

from app import desktop_ui_v2 as v2
from app import desktop_ui_v5 as v5
from app.desktop_message_flow import FlowItem, FlowTurn, build_message_flow


DesktopEventBridge = v5.DesktopEventBridge
ComposerTextEdit = v5.ComposerTextEdit
ThreadListItemWidget = v5.ThreadListItemWidget


_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_STRIKE = re.compile(r"~~(.+?)~~")
_FENCE = re.compile(r"^\s*```([^`]*)\s*$")
_HEADING = re.compile(r"^\s*(#{1,3})\s+(.+)$")
_LIST = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.+)$")
_CHECK = re.compile(r"^\[([ xX])\]\s+(.+)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_FLOW_ACTIVE_STATUSES = {"running", "starting", "streaming", "waiting", "waiting_approval"}


def _inline_markup(text: str) -> str:
    safe = html.escape(text, quote=True)
    slots: dict[str, str] = {}

    def code(match: re.Match[str]) -> str:
        key = f"@@LOOMCODE{len(slots)}@@"
        slots[key] = f"<code>{match.group(1)}</code>"
        return key

    safe = _CODE.sub(code, safe)
    safe = _BOLD.sub(r"<strong>\1</strong>", safe)
    safe = _STRIKE.sub(r"<s>\1</s>", safe)
    for key, value in slots.items():
        safe = safe.replace(key, value)
    return safe


def _rich_blocks(text: str) -> str:
    """Small safe Markdown subset for the native transcript."""
    out: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None
    list_items: list[str] = []
    quoted_lines: list[str] = []
    code_lines: list[str] = []
    language = ""
    in_code = False

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(_inline_markup(x) for x in paragraph) + "</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_kind
        if not list_kind:
            return
        rows: list[str] = []
        for item in list_items:
            checked = _CHECK.match(item.strip())
            if checked:
                done = checked.group(1).casefold() == "x"
                mark = "✓" if done else "○"
                state = "done" if done else "todo"
                body = f"<span class='check {state}'>{mark}</span> {_inline_markup(checked.group(2))}"
            else:
                body = _inline_markup(item)
            rows.append(f"<li>{body}</li>")
        out.append(f"<{list_kind}>" + "".join(rows) + f"</{list_kind}>")
        list_items.clear()
        list_kind = None

    def flush_quote() -> None:
        if quoted_lines:
            out.append(
                "<blockquote>"
                + "<br>".join(_inline_markup(x) for x in quoted_lines)
                + "</blockquote>"
            )
            quoted_lines.clear()

    def flush_text() -> None:
        flush_paragraph()
        flush_list()
        flush_quote()

    for line in text.splitlines():
        fence = _FENCE.match(line)
        if fence:
            if in_code:
                label = html.escape(language, quote=True)
                badge = f"<div class='codeLabel'>{label}</div>" if label else ""
                out.append(
                    f"<div class='codeBlock'>{badge}<pre>"
                    f"{html.escape(chr(10).join(code_lines), quote=False)}</pre></div>"
                )
                code_lines.clear()
                language = ""
                in_code = False
            else:
                flush_text()
                in_code = True
                language = fence.group(1).strip()
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_text()
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_text()
            level = len(heading.group(1))
            out.append(f"<div class='h{level}'>{_inline_markup(heading.group(2))}</div>")
            continue

        quoted = _QUOTE.match(line)
        if quoted:
            flush_paragraph()
            flush_list()
            quoted_lines.append(quoted.group(1))
            continue
        if quoted_lines:
            flush_quote()

        listed = _LIST.match(line)
        if listed:
            flush_paragraph()
            kind = "ol" if listed.group(1)[0].isdigit() else "ul"
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            list_items.append(listed.group(2))
            continue
        if list_kind:
            flush_list()
        paragraph.append(line)

    if in_code:
        label = html.escape(language, quote=True)
        badge = f"<div class='codeLabel'>{label}</div>" if label else ""
        out.append(
            f"<div class='codeBlock'>{badge}<pre>"
            f"{html.escape(chr(10).join(code_lines), quote=False)}</pre></div>"
        )
    flush_text()
    return "".join(out) or "<p></p>"


class LoomDesktopWindow(v5.LoomDesktopWindow):
    """Desktop v6 with a Codex-style durable turn/message activity stream."""

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
        self.resize(1680, 1020)
        self.setMinimumSize(1180, 740)
        self.sidebar_panel.setMinimumWidth(260)
        self.sidebar_panel.setMaximumWidth(350)
        self.activity_panel.setMinimumWidth(304)
        self.activity_panel.setMaximumWidth(410)
        self.main_splitter.setSizes([286, 1058, 336])

        self.sidebar_panel.layout().setContentsMargins(18, 20, 14, 14)
        self.sidebar_panel.layout().setSpacing(14)
        center = self.findChild(QFrame, "conversationPanel")
        if center and center.layout():
            center.layout().setContentsMargins(30, 20, 30, 20)
            center.layout().setSpacing(14)
        self.activity_panel.layout().setContentsMargins(18, 20, 16, 14)
        self.activity_panel.layout().setSpacing(12)
        self.composer_frame.layout().setContentsMargins(16, 13, 12, 11)

        subtitle = self.sidebar_panel.findChild(QLabel, "brandSubtitle")
        if subtitle:
            subtitle.setText("Local agent workspace")
        self.new_thread_button.setText("+  New thread")
        self.open_project_button.setText("Open…")
        self.composer.setPlaceholderText("Message Loom…")
        self.send_button.setText("Send  ↑")
        self.send_button.setMinimumWidth(92)

        empty_kicker = self.findChild(QLabel, "emptyKicker")
        empty_title = self.findChild(QLabel, "emptyTitle")
        empty_body = self.findChild(QLabel, "emptyBody")
        if empty_kicker:
            empty_kicker.setText("LOOM WORKSPACE")
        if empty_title:
            empty_title.setText("Ready when you are")
        if empty_body:
            empty_body.setText(
                "Inspect, edit, run, browse, and coordinate work in this project from one durable thread."
            )
        for button, label in zip(
            self.findChildren(QPushButton, "promptSuggestion"),
            ("Review this project", "Find a real bug", "Run the tests", "Explain architecture"),
        ):
            button.setText(f"{label}   →")
        hint = self.findChild(QLabel, "composerHint")
        if hint:
            hint.setText("Enter to send   ·   Shift+Enter for newline")

        self.activity_view.document().setDocumentMargin(0)
        self.transcript.document().setDocumentMargin(0)
        self.transcript.setOpenExternalLinks(False)
        self.transcript.anchorClicked.connect(self._on_flow_anchor)
        for button in self.findChildren(QPushButton):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.composer_frame.setProperty("focused", False)
        v2._repolish(self.composer_frame)

        self._sidebar_motion_bounds = (
            self.sidebar_panel.minimumWidth(),
            self.sidebar_panel.maximumWidth(),
        )
        self._runtime_motion_bounds = (
            self.activity_panel.minimumWidth(),
            self.activity_panel.maximumWidth(),
        )
        self._sidebar_restore_width = 286
        self._runtime_restore_width = 336

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self.composer and hasattr(self, "composer_frame"):
            if event.type() == QEvent.Type.FocusIn:
                self.composer_frame.setProperty("focused", True)
                v2._repolish(self.composer_frame)
            elif event.type() == QEvent.Type.FocusOut:
                self.composer_frame.setProperty("focused", False)
                v2._repolish(self.composer_frame)
        return super().eventFilter(watched, event)

    def _apply_style(self) -> None:
        super()._apply_style()
        self.setStyleSheet(
            self.styleSheet()
            + """
        QMainWindow, QWidget { background:#090a0e; color:#eef1f6; font-family:"Segoe UI Variable","Segoe UI",sans-serif; font-size:13px; }
        QFrame#sidebar { background:#0b0d12; border-right:1px solid #181c24; }
        QFrame#activityPanel { background:#0b0d12; border-left:1px solid #181c24; }
        QFrame#conversationPanel, QTextBrowser#transcript { background:#090a0e; }
        QLabel#brandMark { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #7b71ef,stop:1 #5e56c8); border:1px solid #8278ed; border-radius:10px; color:white; font-weight:800; }
        QLabel#brandLabel { color:#f5f6fa; font-size:18px; font-weight:700; }
        QLabel#brandSubtitle, QLabel#mutedLabel { color:#626b7d; font-size:10px; }
        QLabel#sectionLabel { color:#697184; font-size:9px; font-weight:750; letter-spacing:1.5px; }
        QPushButton { min-height:34px; background:#11141b; border:1px solid #232833; border-radius:9px; padding:1px 11px; color:#cbd0da; font-weight:600; }
        QPushButton:hover { background:#161a22; border-color:#313746; color:#f2f4f8; }
        QPushButton:pressed { background:#0f1218; }
        QPushButton:disabled { color:#4e5564; background:#0d0f14; border-color:#171b22; }
        QPushButton#newThreadButton, QPushButton#sendButton, QPushButton#allowButton { background:#6b62dc; border-color:#7b72ea; color:white; font-weight:700; }
        QPushButton#newThreadButton:hover, QPushButton#sendButton:hover, QPushButton#allowButton:hover { background:#766ce8; border-color:#8a81f1; }
        QPushButton#openProjectButton { min-width:66px; background:#0e1117; }
        QPushButton#iconButton { min-width:28px; max-width:28px; min-height:28px; max-height:28px; padding:0; background:transparent; border-color:transparent; color:#687183; }
        QPushButton#iconButton:hover { background:#12151c; color:#d9dce3; border-color:#20252f; }
        QListWidget#threadList { background:transparent; border:none; outline:none; padding:1px 1px 1px 0; }
        QListWidget#threadList::item { background:transparent; border:1px solid transparent; border-left:2px solid transparent; border-radius:10px; margin:2px 0; }
        QListWidget#threadList::item:hover { background:#10131a; border-color:#181d26; }
        QListWidget#threadList::item:selected { background:#141620; border:1px solid #292d3d; border-left:2px solid #7168e2; }
        QLabel#threadItemTitle { color:#e3e6ec; font-size:12px; font-weight:650; }
        QLabel#threadItemMeta { color:#596274; font-size:9px; }
        QLabel#threadStatus[state="idle"], QLabel#threadStatus[state="completed"] { background:transparent; border:none; padding:0; color:#647080; font-size:9px; }
        QLabel#threadStatus[state="completed"] { color:#6f9888; }
        QLabel#connectionDot { color:#5dc89a; font-size:8px; }
        QLabel#protocolLabel { color:#5d6677; font-size:9px; }
        QFrame#workspaceHeader { border-bottom:1px solid #171b23; }
        QLabel#threadTitle { color:#f3f4f8; font-size:20px; font-weight:680; }
        QLabel#projectName { color:#a9afba; font-size:10px; font-weight:650; }
        QPushButton#panelToggle { min-width:28px; max-width:30px; min-height:28px; max-height:28px; padding:0; background:transparent; border:1px solid transparent; border-radius:7px; color:#626b7c; font-size:17px; }
        QPushButton#panelToggle:hover { background:#11141b; border-color:#1e232d; color:#e1e3e8; }
        QPushButton#panelToggle[active="true"] { color:#8b85e7; }
        QLabel#statusChip[state="completed"] { background:#0f1916; color:#83c8ad; }
        QFrame#emptyStateContent { min-width:500px; max-width:650px; }
        QLabel#emptyKicker { color:#7d74e8; font-size:9px; font-weight:800; letter-spacing:1.7px; }
        QLabel#emptyTitle { color:#f0f2f6; font-size:26px; font-weight:700; }
        QLabel#emptyBody { color:#737b8b; font-size:12px; }
        QPushButton#promptSuggestion { background:#0d1016; border:1px solid #1c212b; border-radius:10px; min-height:42px; color:#aab0bc; text-align:left; padding:0 13px; }
        QPushButton#promptSuggestion:hover { background:#12161d; border-color:#2a303c; color:#f0f1f5; }
        QFrame#composerFrame { background:#0d1016; border:1px solid #242a35; border-radius:15px; }
        QFrame#composerFrame[focused="true"] { background:#0f1219; border-color:#4a486d; }
        QTextEdit#composer { background:transparent; border:none; color:#f0f2f6; font-size:14px; padding:3px 2px 5px 2px; }
        QLabel#composerHint, QLabel#composerState { color:#555e70; font-size:9px; }
        QLabel#composerChip { color:#727b8c; background:#11151c; border:1px solid #1b2029; border-radius:7px; padding:2px 7px; font-size:9px; }
        QLabel#inspectorTitle { color:#f0f2f5; font-size:17px; font-weight:700; }
        QTabBar::tab { color:#677082; padding:9px 8px; border:none; border-bottom:2px solid transparent; font-size:10px; font-weight:600; }
        QTabBar::tab:hover { color:#b7bdc8; }
        QTabBar::tab:selected { color:#edeaff; border-bottom-color:#7168e2; }
        QTextBrowser#activityView, QPlainTextEdit#terminalView, QPlainTextEdit#diffView, QPlainTextEdit#browserView, QPlainTextEdit#agentsView { background:#090b10; border:1px solid #171c24; border-radius:10px; color:#adb4c0; padding:8px; font-family:"Cascadia Mono","Consolas",monospace; font-size:10px; }
        QSplitter::handle { background:#171b23; width:1px; }
        QScrollBar:vertical { background:transparent; width:7px; margin:3px 1px; }
        QScrollBar::handle:vertical { background:#262c36; border-radius:3px; min-height:36px; }
        QScrollBar::handle:vertical:hover { background:#3a414e; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; border:none; }
        """
        )

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
                if isinstance(item, dict) and v2._text(item.get("id")):
                    durable_ids.add(v2._text(item.get("id")))
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
            if item_id and v2._text(item.get("type")) not in {"assistant_message", "user_message"}:
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
                self._flow_live_items[item_id] = dict(item)
        elif method == "approval/requested":
            approval = params.get("approval") or {}
            if isinstance(approval, dict):
                call_id = v2._text(approval.get("callId"))
                if call_id:
                    item_id = v2._text(approval.get("itemId")) or f"approval:{call_id}"
                    self._flow_live_items[item_id] = {
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
            turn_rows = self._turn_flow_rows(turn)
            rows.extend(turn_rows)
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
            f"<a href='loom://turn/{turn_id}'><span class='elapsed'>{html.escape(turn.elapsed())}</span> "
            f"<span class='turnState'>{status}</span> <span class='chevron'>{chevron}</span></a>"
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

    @staticmethod
    def _message_card(role: str, content: str, *, streaming: bool) -> str:
        label = "YOU" if role == "user" else "LOOM"
        live = " <span class='stream'>· WORKING</span>" if streaming else ""
        return (
            f"<div class='message {role}'><div class='meta'>{label}{live}</div>"
            f"<div class='body'>{_rich_blocks(content)}</div></div>"
        )

    def _render_activity(self) -> None:
        rows: list[str] = []
        for when, marker, summary in self._activity_tail[-300:]:
            tone = (
                "good"
                if marker == "✓"
                else "bad"
                if marker == "!"
                else "accent"
                if marker in {"◆", "◇", "Δ"}
                else "muted"
            )
            rows.append(
                f"<div class='event'><span class='marker {tone}'>{html.escape(marker)}</span>"
                f"<span class='summary'>{html.escape(summary)}</span>"
                f"<div class='time'>{html.escape(when or 'live')}</div></div>"
            )
        if not rows:
            rows.append(
                "<div class='quiet'><b>Runtime is quiet</b><br>"
                "<span>Model steps, tools, commands, diffs, and delegated work will appear here.</span></div>"
            )
        self.activity_view.setHtml(
            """
        <style>body{font-family:'Segoe UI',sans-serif;background:#090b10;color:#aeb4c0;margin:5px 7px 16px;font-size:10px}.event{border-bottom:1px solid #181d25;padding:10px 3px}.marker{display:inline-block;width:18px;font-weight:800;margin-right:7px}.good{color:#74bd9d}.bad{color:#df8e98}.accent{color:#8a82e8}.muted{color:#6f7788}.summary{color:#c3c8d1}.time{color:#555e6f;font-family:'Consolas',monospace;font-size:8px;margin:3px 0 0 25px}.quiet{margin:50px 10px;color:#d0d3da}.quiet span{color:#666f80}</style>
        """
            + "".join(rows)
        )
        cursor = self.activity_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.activity_view.setTextCursor(cursor)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._flow_timer is not None:
            self._flow_timer.stop()
        super().closeEvent(event)
