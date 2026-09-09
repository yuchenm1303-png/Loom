"""Final compact disclosure treatment for transcript task rows.

The main transcript should read like a concise execution log, not a stack of
status-coloured cards.  Runtime state still matters, but it is expressed through
small inline text/spinner cues rather than full red/amber/blue outlines.

This layer intentionally runs after ``runtime_feedback`` so it can keep that
module's live-state semantics while normalising the final presentation:
- no category separator bars between command/tool/file rows;
- one quiet one-line disclosure header per activity;
- neutral expanded detail surface for every status;
- failure/waiting state is inline text only, never a red container border;
- the duplicated footer status is hidden in the central transcript;
- failures that never became a live process remain collapsed until requested.
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
/* The transcript task flow is a disclosure list, not a coloured card stack. */
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
    border-radius:6px;
}
QFrame#activityCard[disclosureInteractive="true"]:hover,
QFrame#activityCard[runtimeState="running"]:hover,
QFrame#activityCard[runtimeState="waiting"]:hover,
QFrame#activityCard[runtimeState="failed"]:hover {
    background:#14171c;
    border:none;
}
QFrame#activityCard[disclosurePressed="true"] {
    background:#191c22;
    border:none;
}
QFrame#activityCard[runtimeState="running"] {
    background:#11151b;
}
QFrame#activityCard[runtimeState="waiting"] {
    background:#15140f;
}
QFrame#activityCard[runtimeState="failed"] {
    background:transparent;
}

/* Expanded details use one neutral shell regardless of success/failure. */
QFrame#cardBodyShell,
QFrame#cardBodyShell[state="completed"],
QFrame#cardBodyShell[state="running"],
QFrame#cardBodyShell[state="started"],
QFrame#cardBodyShell[state="waiting"],
QFrame#cardBodyShell[state="waiting_approval"],
QFrame#cardBodyShell[state="failed"],
QFrame#cardBodyShell[state="denied"],
QFrame#cardBodyShell[state="cancelled"] {
    background:#1b1c20;
    border:1px solid #3a3c42;
    border-radius:10px;
}
QLabel#cardBodyTitle {
    background:transparent;
    border:none;
    color:#adb2bc;
    font-size:11px;
    font-weight:620;
}
QPlainTextEdit#cardBody {
    background:transparent;
    border:none;
    color:#c6cad1;
    padding:2px 1px 1px 1px;
}
QLabel#cardStatus {
    background:transparent;
    border:none;
    padding:0;
}
"""


_BADGE_QSS = r"""
QFrame#activityLiveBadge,
QFrame#activityLiveBadge[tone="waiting"],
QFrame#activityLiveBadge[tone="failed"] {
    background:transparent;
    border:none;
    border-radius:0;
}
QLabel#activityLiveText {
    background:transparent;
    border:none;
    color:#8f97a5;
    font-size:10px;
    font-weight:620;
    padding:0;
}
QFrame#activityLiveBadge[tone="waiting"] QLabel#activityLiveText {
    color:#c7a86e;
}
QFrame#activityLiveBadge[tone="failed"] QLabel#activityLiveText {
    color:#cf8793;
}
"""


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
    layout = badge.layout()
    if layout is not None:
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

    spinner = getattr(badge, "spinner", None)
    if spinner is not None:
        # A spinner is useful while work is truly active.  Waiting/failure only
        # need the short text label; a static ring makes those rows look busier.
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
        # Consecutive actions should read like one compact task stream.  A tiny
        # extra gap at semantic boundaries is enough; no horizontal rule needed.
        outer.setContentsMargins(0, 0, 0, 4 if end else 1)
        outer.setSpacing(5)
    card.updateGeometry()


def install() -> None:
    """Install the compact task-flow presentation once."""
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
            outer.setSpacing(5)

        header = _header_layout(self)
        if header is not None:
            header.setContentsMargins(2, 2, 2, 2)
            header.setSpacing(6)

        self.icon.setFixedSize(16, 16)
        self.toggle_button.setFixedSize(18, 18)

        shell_layout = self.body_shell.layout()
        if shell_layout is not None:
            shell_layout.setContentsMargins(11, 8, 11, 8)
            shell_layout.setSpacing(5)

        section = getattr(self, "_activity_section", None)
        if section is not None:
            section.hide()

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
        original_update(
            self,
            title=title,
            subtitle=subtitle,
            status=status,
            body=body,
            auto_expand=auto_expand,
        )

        status = fmt.text(status).strip()
        self.status_label.hide()
        section = getattr(self, "_activity_section", None)
        if section is not None:
            section.hide()
        _style_runtime_badge(self, status)

        lowered = fmt.text(title).strip().casefold()
        if self.kind == "process" or lowered in {"exec", "exec_write", "run_command", "shell", "terminal"}:
            self.body_title.setText("Shell")
        elif self.kind == "diff":
            self.body_title.setText("Changes")
        elif self.kind == "error":
            self.body_title.setText("Error")
        else:
            self.body_title.setText("Details")

        # A tool-level failure should announce itself in the row but not explode
        # into a large red panel automatically.  If a real live process was
        # already open, keep its final output open so the user does not lose
        # context when it exits.
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
