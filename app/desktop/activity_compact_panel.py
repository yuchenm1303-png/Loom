"""Refined compact disclosure treatment for transcript task rows.

The main transcript should read like a concise execution log, not a stack of
status-coloured cards. Runtime state is expressed through a small status chip,
semantic icon and disclosure affordance rather than full-width warning chrome.

This layer intentionally runs after ``runtime_feedback`` so it can preserve live
state semantics while applying the final product presentation:
- no category separator bars between command/tool/file rows;
- one compact, optically centred disclosure header per activity;
- neutral expanded detail surface for every status;
- failure/waiting state lives in a small status chip, never a red container;
- common internal tool names are presented as human actions;
- duplicated footer status remains hidden in the central transcript;
- failures that never became a live process stay collapsed until requested.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from app.desktop import activity_hierarchy as hierarchy
from app.desktop import format as fmt
from app.desktop import output_presentation as presentation
from app.desktop import widgets as base


_INSTALLED = False
_FAILED = {"failed", "denied", "cancelled"}
_WAITING = {"waiting", "waiting_approval"}
_ACTIVE = {"running", "started"}


_COMPACT_CARD_QSS = r"""
/* Quiet one-line task rows. State never paints the whole row red/amber. */
QFrame#activityCard,
QFrame#activityCard[state="completed"],
QFrame#activityCard[state="running"],
QFrame#activityCard[state="started"],
QFrame#activityCard[state="waiting"],
QFrame#activityCard[state="waiting_approval"],
QFrame#activityCard[state="failed"],
QFrame#activityCard[state="denied"],
QFrame#activityCard[state="cancelled"],
QFrame#activityCard[runtimeState="running"],
QFrame#activityCard[runtimeState="waiting"],
QFrame#activityCard[runtimeState="failed"] {
    background:transparent;
    border:none;
    border-radius:8px;
    padding:0;
    margin:0;
}
QFrame#activityCard[disclosureInteractive="true"]:hover,
QFrame#activityCard[runtimeState="running"]:hover,
QFrame#activityCard[runtimeState="waiting"]:hover,
QFrame#activityCard[runtimeState="failed"]:hover {
    background:#12151b;
    border:none;
}
QFrame#activityCard[disclosurePressed="true"] {
    background:#171a21;
    border:none;
}
QFrame#activityCard[runtimeState="running"] {
    background:#10141a;
}
QFrame#activityCard[runtimeState="waiting"],
QFrame#activityCard[runtimeState="failed"] {
    background:transparent;
}
QLabel#cardTitle {
    background:transparent;
    border:none;
    color:#e6e9ef;
    font-size:12px;
    font-weight:600;
    padding:0;
    margin:0;
}
QLabel#cardSubtitle {
    background:transparent;
    border:none;
    color:#858d9b;
    font-size:10px;
    padding:0;
    margin:0;
}

/* Expanded details are a single calm inset surface, independent of status. */
QFrame#cardBodyShell,
QFrame#cardBodyShell[state="completed"],
QFrame#cardBodyShell[state="running"],
QFrame#cardBodyShell[state="started"],
QFrame#cardBodyShell[state="waiting"],
QFrame#cardBodyShell[state="waiting_approval"],
QFrame#cardBodyShell[state="failed"],
QFrame#cardBodyShell[state="denied"],
QFrame#cardBodyShell[state="cancelled"] {
    background:#17191e;
    border:1px solid #30333b;
    border-radius:9px;
}
QLabel#cardBodyTitle {
    background:transparent;
    border:none;
    color:#9fa6b2;
    font-size:10px;
    font-weight:650;
    padding:0;
}
QPlainTextEdit#cardBody {
    background:transparent;
    border:none;
    color:#c7cbd2;
    padding:1px 0 0 0;
    selection-background-color:#343846;
}
QLabel#cardStatus {
    background:transparent;
    border:none;
    padding:0;
}
"""


_BADGE_QSS = r"""
QFrame#activityLiveBadge {
    background:#171a22;
    border:1px solid #292e3a;
    border-radius:8px;
}
QFrame#activityLiveBadge[tone="waiting"] {
    background:#1d1911;
    border-color:#3b3120;
}
QFrame#activityLiveBadge[tone="failed"] {
    background:#211519;
    border-color:#3b242b;
}
QLabel#activityLiveText {
    background:transparent;
    border:none;
    color:#9ea8bc;
    font-size:9px;
    font-weight:680;
    padding:0;
}
QFrame#activityLiveBadge[tone="waiting"] QLabel#activityLiveText {
    color:#d0ad6e;
}
QFrame#activityLiveBadge[tone="failed"] QLabel#activityLiveText {
    color:#d98793;
}
"""


_TOOL_TITLES = {
    "exec": "Run command",
    "run_command": "Run command",
    "shell": "Run command",
    "terminal": "Run command",
    "exec_write": "Send command input",
    "computer_status": "Check computer",
    "memory_status": "Check memory",
    "browser_navigate": "Open page",
    "browser_open": "Open browser",
    "web_search": "Search web",
    "read_file": "Read file",
    "read_workspace_text": "Read file",
    "write_workspace_text": "Edit file",
    "edit_file": "Edit file",
    "apply_patch": "Apply changes",
}


def _friendly_action_title(title: str, kind: str) -> str:
    """Turn transport/tool identifiers into concise user-facing actions."""
    raw = fmt.text(title).strip()
    if kind != "tool":
        return raw
    return _TOOL_TITLES.get(raw.casefold(), raw)


def _header_layout(card: Any) -> Any | None:
    outer = card.layout()
    if outer is None:
        return None
    for index in range(outer.count()):
        layout = outer.itemAt(index).layout()
        if layout is not None and layout.indexOf(card.toggle_button) >= 0:
            return layout
    return None


def _style_runtime_badge(card: Any, status: str = "") -> None:
    badge = getattr(card, "runtime_badge", None)
    if badge is None:
        return

    badge.setStyleSheet(_BADGE_QSS)
    badge.setFixedHeight(20)
    layout = badge.layout()
    if layout is not None:
        layout.setContentsMargins(6, 1, 6, 1)
        layout.setSpacing(4)

    spinner = getattr(badge, "spinner", None)
    if spinner is not None:
        spinner.setFixedSize(10, 10)
        # Motion belongs only to genuinely active work. Static waiting/failure
        # states use the chip text alone so the row stays visually quiet.
        spinner.setVisible(status in _ACTIVE)


def _compact_set_group(card: Any, category: str, *, start: bool, end: bool) -> None:
    """Keep semantic grouping data but remove visible category divider bars."""
    hierarchy._ensure_section_header(card)
    section = getattr(card, "_activity_section", None)
    if section is not None:
        section.hide()

    card.setProperty("activityCategory", category)
    card.setProperty("activityGroupStart", start)
    card.setProperty("activityGroupEnd", end)
    base.repolish(card)

    outer = card.layout()
    if outer is not None:
        # Adjacent actions stay tightly grouped; only semantic block boundaries
        # receive a few pixels of breathing room.
        outer.setContentsMargins(0, 0, 0, 4 if end else 1)
        outer.setSpacing(4)
    card.updateGeometry()


def install() -> None:
    """Install the refined compact task-flow presentation once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_init = presentation.FlatActivityCard.__init__
    original_update = presentation.FlatActivityCard.update_card

    def card_init(self: Any, kind: str, parent: QWidget | None = None) -> None:
        original_init(self, kind, parent)
        self.setStyleSheet(self.styleSheet() + "\n" + _COMPACT_CARD_QSS)

        outer = self.layout()
        if outer is not None:
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(4)

        header = _header_layout(self)
        if header is not None:
            # A compact 28px-ish row: enough hit area without the old slab-like
            # 50-60px task card feel.
            header.setContentsMargins(6, 4, 4, 4)
            header.setSpacing(7)

        self.icon.setFixedSize(15, 15)
        self.toggle_button.setFixedSize(20, 20)
        # The global QPushButton style has a 34px minimum height. Explicitly
        # reset it here so the disclosure control cannot inflate the whole row.
        self.toggle_button.setStyleSheet(
            "QPushButton#activityDisclosure {"
            " min-width:20px; max-width:20px; min-height:20px; max-height:20px;"
            " background:transparent; border:none; border-radius:6px;"
            " padding:0; margin:0;"
            "}"
            "QPushButton#activityDisclosure:hover { background:#1a1e25; }"
            "QPushButton#activityDisclosure:pressed { background:#20252d; }"
        )

        shell_layout = self.body_shell.layout()
        if shell_layout is not None:
            shell_layout.setContentsMargins(12, 9, 12, 9)
            shell_layout.setSpacing(6)

        section = getattr(self, "_activity_section", None)
        if section is not None:
            section.hide()

        self.subtitle_label.hide()
        # The live badge is the single status location in the collapsed row.
        # The old footer label duplicated Failed/Running at the bottom of output.
        self.status_label.hide()
        _style_runtime_badge(self)
        self.updateGeometry()

    def card_update(
        self: Any,
        *,
        title: str,
        subtitle: str = "",
        status: str = "",
        body: str = "",
        auto_expand: bool | None = None,
    ) -> None:
        raw_title = fmt.text(title).strip()
        display_title = _friendly_action_title(raw_title, self.kind)
        original_update(
            self,
            title=display_title,
            subtitle=subtitle,
            status=status,
            body=body,
            auto_expand=auto_expand,
        )

        status = fmt.text(status).strip()
        self.subtitle_label.hide()
        self.status_label.hide()
        section = getattr(self, "_activity_section", None)
        if section is not None:
            section.hide()
        _style_runtime_badge(self, status)

        # Keep the original technical identifier discoverable without putting it
        # back into the visual hierarchy.
        if raw_title and raw_title != display_title:
            self.title_label.setToolTip(raw_title)

        lowered = raw_title.casefold()
        if self.kind == "process" or lowered in {"exec", "exec_write", "run_command", "shell", "terminal"}:
            self.body_title.setText("Shell")
        elif self.kind == "diff":
            self.body_title.setText("Changes")
        elif self.kind == "error":
            self.body_title.setText("Error")
        else:
            self.body_title.setText("Details")

        # A tool-level failure should announce itself in the compact chip but not
        # explode into a large panel automatically. A real live process that was
        # already open keeps its final output so context is not lost on exit.
        live_process_was_open = bool(getattr(self, "_runtime_auto_opened", False))
        if (
            status in _FAILED
            and bool(body)
            and not self._user_toggled
            and not live_process_was_open
        ):
            self._expanded = False
            self._sync_body(animate=False)

        self.updateGeometry()

    hierarchy._set_group = _compact_set_group
    presentation.FlatActivityCard.__init__ = card_init
    presentation.FlatActivityCard.update_card = card_update


__all__ = ["install"]
