from __future__ import annotations

import html

from PySide6.QtGui import QTextCursor

from app import desktop_ui_v2 as v2
from app import desktop_ui_v8 as v8


DesktopEventBridge = v8.DesktopEventBridge
ComposerTextEdit = v8.ComposerTextEdit
ThreadListItemWidget = v8.ThreadListItemWidget


_UI_TYPOGRAPHY_QSS = """
QMainWindow, QWidget {
    font-family:"Segoe UI Variable Text","Segoe UI Variable","Microsoft YaHei UI","Microsoft YaHei","Segoe UI",sans-serif;
    font-size:14px;
}
QLabel#brandLabel { font-size:19px; font-weight:700; }
QLabel#brandSubtitle, QLabel#mutedLabel { font-size:11px; }
QLabel#sectionLabel { font-size:10px; letter-spacing:1.35px; }
QPushButton { min-height:36px; font-size:13px; }
QPushButton#iconButton { font-size:13px; }
QLabel#threadItemTitle { font-size:14px; font-weight:650; }
QLabel#threadItemMeta { font-size:10px; }
QLabel#threadStatus { font-size:10px; }
QLabel#connectionDot { font-size:9px; }
QLabel#protocolLabel { font-size:10px; }
QLabel#threadTitle { font-size:22px; font-weight:680; }
QLabel#projectName { font-size:11px; }
QLabel#statusChip { font-size:10px; padding:3px 8px; }
QLabel#emptyKicker { font-size:10px; }
QLabel#emptyTitle { font-size:28px; }
QLabel#emptyBody { font-size:13px; }
QPushButton#promptSuggestion { min-height:44px; font-size:13px; }
QTextEdit#composer {
    font-family:"Segoe UI Variable Text","Segoe UI Variable","Microsoft YaHei UI","Microsoft YaHei","Segoe UI",sans-serif;
    font-size:15px;
}
QLabel#composerHint, QLabel#composerState { font-size:10px; }
QLabel#composerChip { font-size:10px; }
QLabel#inspectorTitle { font-size:18px; }
QTabBar::tab { font-size:11px; padding:10px 9px; }
QTextBrowser#activityView,
QPlainTextEdit#terminalView,
QPlainTextEdit#diffView,
QPlainTextEdit#browserView,
QPlainTextEdit#agentsView {
    font-family:"Cascadia Mono","Cascadia Code","Microsoft YaHei UI","Consolas",monospace;
    font-size:12px;
}
QLineEdit#threadSearch {
    min-height:34px;
    font-family:"Segoe UI Variable Text","Segoe UI Variable","Microsoft YaHei UI","Microsoft YaHei","Segoe UI",sans-serif;
    font-size:12px;
}
QPushButton#archiveViewButton { min-height:34px; font-size:11px; }
QPushButton#threadActionsButton { min-height:34px; max-height:34px; font-size:13px; }
QMenu {
    font-family:"Segoe UI Variable Text","Segoe UI Variable","Microsoft YaHei UI","Microsoft YaHei","Segoe UI",sans-serif;
    font-size:12px;
}
QMenu::item { padding:8px 17px 8px 11px; }
"""

_TRANSCRIPT_STYLE = """
<style>
body{
    color:#e2e5eb;
    font-family:'Segoe UI Variable Text','Segoe UI Variable','Microsoft YaHei UI','Microsoft YaHei','Segoe UI',sans-serif;
    font-size:16px;
    margin:20px 12px 42px;
    background:#090a0e;
}
.message{margin:0 0 30px}
.meta{
    color:#727b8d;
    font-size:10px;
    font-weight:700;
    letter-spacing:1.05px;
    margin:0 0 8px 1px;
}
.assistant .meta{color:#9189f0}
.stream{color:#aaa4f6;font-size:9px}
.body{line-height:1.66}
.user .body{
    background:#11141b;
    border:1px solid #252a35;
    border-radius:12px;
    padding:12px 14px;
    margin-left:48px;
}
p{margin:0 0 11px}
strong{color:#f5f6f9}
code{
    background:#151923;
    color:#d8d4ff;
    font-family:'Cascadia Mono','Cascadia Code','Microsoft YaHei UI','Consolas',monospace;
    font-size:13px;
}
.h1,.h2,.h3{color:#f4f5f8;font-weight:700;margin:15px 0 9px}
.h1{font-size:21px}
.h2{font-size:18px}
.h3{font-size:16px}
ul,ol{margin:5px 0 12px 20px}
.check{font-weight:800}
.check.done{color:#78c3a2}
.check.todo{color:#727b8b}
blockquote{
    color:#b3b9c4;
    border-left:2px solid #413d5c;
    margin:9px 0 12px;
    padding:4px 0 4px 12px;
}
.codeBlock{
    background:#0c0f14;
    border:1px solid #202631;
    border-radius:9px;
    margin:9px 0 13px;
}
.codeLabel{
    color:#70798b;
    font-family:'Cascadia Mono','Consolas',monospace;
    font-size:10px;
    padding:8px 11px 0;
}
pre{
    color:#ccd1da;
    background:transparent;
    font-family:'Cascadia Mono','Cascadia Code','Microsoft YaHei UI','Consolas',monospace;
    font-size:12px;
    line-height:1.55;
    margin:0;
    padding:10px 11px 11px;
    white-space:pre-wrap;
}
</style>
"""

_ACTIVITY_STYLE = """
<style>
body{
    font-family:'Segoe UI Variable Text','Segoe UI Variable','Microsoft YaHei UI','Microsoft YaHei','Segoe UI',sans-serif;
    background:#090b10;
    color:#b5bbc6;
    margin:6px 8px 18px;
    font-size:12px;
}
.event{border-bottom:1px solid #181d25;padding:11px 3px}
.marker{display:inline-block;width:19px;font-weight:800;margin-right:7px}
.good{color:#74bd9d}
.bad{color:#df8e98}
.accent{color:#8a82e8}
.muted{color:#737b8c}
.summary{color:#cbd0d8;line-height:1.45}
.time{
    color:#616a7b;
    font-family:'Cascadia Mono','Consolas',monospace;
    font-size:9px;
    margin:4px 0 0 26px;
}
.quiet{margin:50px 10px;color:#d4d7dd;font-size:13px}
.quiet span{color:#70798a}
</style>
"""


class LoomDesktopWindow(v8.LoomDesktopWindow):
    """Desktop v9: readability-first typography for dense desktop work."""

    def _apply_style(self) -> None:
        super()._apply_style()
        self.setStyleSheet(self.styleSheet() + _UI_TYPOGRAPHY_QSS)

    def _render_transcript(self) -> None:
        was_empty = self.empty_state.isVisible() if hasattr(self, "empty_state") else False
        was_transcript = self.transcript.isVisible() if hasattr(self, "transcript") else False
        cards: list[str] = []
        for message in self._durable_messages:
            role = v2._text(message.get("role"))
            content = v2._text(message.get("content"))
            if role in {"user", "assistant"} and content:
                cards.append(self._message_card(role, content, streaming=False))
        if self._optimistic_user:
            cards.append(self._message_card("user", self._optimistic_user, streaming=True))
        for text in self._live_assistant.values():
            cards.append(self._message_card("assistant", text or "…", streaming=True))

        count = len(cards)
        previous = self._last_transcript_card_count
        if not cards:
            self.transcript.hide()
            self.empty_state.show()
        else:
            self.empty_state.hide()
            self.transcript.show()
            bar = self.transcript.verticalScrollBar()
            follow = bar.maximum() - bar.value() <= 56
            old = bar.value()
            self.transcript.setHtml(_TRANSCRIPT_STYLE + "".join(cards))
            if follow:
                cursor = self.transcript.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                self.transcript.setTextCursor(cursor)
            else:
                bar.setValue(min(old, bar.maximum()))

        self._last_transcript_card_count = count
        if self.empty_state.isVisible() and not was_empty:
            self._fade_in_widget(self.empty_state, "empty-state", start=0.12, duration=230)
        if self.transcript.isVisible() and not was_transcript:
            self._fade_in_widget(self.transcript, "transcript", start=0.20, duration=175)
        elif self.transcript.isVisible() and count > previous:
            self._pulse_widget(self.transcript, "new-message", start=0.91, duration=105)

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
        self.activity_view.setHtml(_ACTIVITY_STYLE + "".join(rows))
        cursor = self.activity_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.activity_view.setTextCursor(cursor)


__all__ = [
    "ComposerTextEdit",
    "DesktopEventBridge",
    "LoomDesktopWindow",
    "ThreadListItemWidget",
]
