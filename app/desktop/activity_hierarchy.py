"""Semantic hierarchy for the main transcript's execution activity.

Dense rows are useful only when the eye can still tell what belongs together.
This presentation pass keeps the compact log density, but gives consecutive
commands, file changes, tools, and errors clear section boundaries. It also
removes redundant request rows when a richer process/diff result already tells
the same story.

The Runtime inspector deliberately keeps its full event stream; these rules are
only for the central conversation transcript.
"""

from __future__ import annotations

import re
from typing import Any

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget

from app.desktop import format as fmt
from app.desktop import output_presentation as presentation
from app.desktop import widgets as base
from app.desktop.state import TranscriptEntry


_INSTALLED = False
_ACTIVITY_KINDS = {"process", "diff", "tool", "error"}

# These tool-call rows are transport detail when the transcript already contains
# the managed process or file diff that resulted from them. Keep exceptional /
# live states visible because they still need the reader's attention.
_PROCESS_BACKED_TOOLS = {
    "exec",
    "exec_write",
    "run_command",
    "shell",
    "terminal",
}
_FILE_BACKED_TOOLS = {
    "apply_patch",
    "create_file",
    "delete_file",
    "edit_file",
    "replace_file",
    "write_file",
    "write_workspace_text",
}
_ATTENTION_STATUSES = {
    "running",
    "started",
    "waiting",
    "waiting_approval",
    "failed",
    "denied",
    "cancelled",
}

_SECTION_META = {
    "command": ("Commands", "#7f8999"),
    "file": ("Files", "#9b8af0"),
    "tool": ("Tools", "#7fa5cf"),
    "issue": ("Issues", "#d4838b"),
}


def _tool_name(entry: TranscriptEntry) -> str:
    return fmt.text(entry.item.get("toolName")).strip().casefold()


def _has_following_kind(entries: list[TranscriptEntry], index: int, kind: str) -> bool:
    """Look within the current model/action phase for the richer result row."""
    for candidate in entries[index + 1 :]:
        if candidate.kind in {"assistant", "user"}:
            return False
        if candidate.kind == kind:
            return True
    return False


def _coalesce_activity(entries: list[TranscriptEntry]) -> list[TranscriptEntry]:
    """Hide duplicate plumbing rows while preserving anything actionable."""
    visible: list[TranscriptEntry] = []
    for index, entry in enumerate(entries):
        if entry.kind != "tool" or entry.status in _ATTENTION_STATUSES:
            visible.append(entry)
            continue

        name = _tool_name(entry)
        if name in _PROCESS_BACKED_TOOLS and _has_following_kind(entries, index, "process"):
            continue
        if name in _FILE_BACKED_TOOLS and _has_following_kind(entries, index, "diff"):
            continue
        visible.append(entry)
    return visible


def _category(entry: TranscriptEntry | None) -> str | None:
    if entry is None:
        return None
    if entry.kind == "process":
        return "command"
    if entry.kind == "diff":
        return "file"
    if entry.kind == "error":
        return "issue"
    if entry.kind == "tool":
        return "tool"
    return None


def _basename(value: str) -> str:
    value = value.strip().strip("\"'")
    parts = re.split(r"[\\/]", value)
    return parts[-1] if parts else value


def _compact_process_title(value: str) -> str:
    """Remove shell plumbing while keeping the useful command identity."""
    value = value.strip()
    match = re.match(r"^(Ran|Running)\s+cmd\s+/c\s+(.+)$", value, re.IGNORECASE)
    if match:
        return f"{match.group(1)} {match.group(2)}"

    file_match = re.match(
        r"^(Ran|Running)\s+(?:powershell(?:\.exe)?)\b.*?\s-File\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))",
        value,
        re.IGNORECASE,
    )
    if file_match:
        path = next((part for part in file_match.groups()[1:] if part), "")
        if path:
            return f"{file_match.group(1)} {_basename(path)}"
    return value


def _friendly_tool_name(value: str) -> str:
    value = value.strip()
    if not value or "_" not in value:
        return value
    return value.replace("_", " ").strip().capitalize()


def _ensure_section_header(card: Any) -> None:
    if getattr(card, "_activity_section", None) is not None:
        return
    outer = card.layout()
    if outer is None:
        return

    section = QWidget(card)
    section.setObjectName("activitySection")
    row = QHBoxLayout(section)
    # Align the label with row titles (16px icon + 5px header spacing).
    row.setContentsMargins(21, 7, 0, 2)
    row.setSpacing(8)

    label = QLabel("", section)
    label.setObjectName("activitySectionLabel")
    label.setStyleSheet(
        "background:transparent; border:none; font-size:9px; font-weight:700; "
        "letter-spacing:0.5px; padding:0; margin:0;"
    )
    row.addWidget(label, 0)

    rule = QFrame(section)
    rule.setObjectName("activitySectionRule")
    rule.setFixedHeight(1)
    rule.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    rule.setStyleSheet("background:#242935; border:none; margin:0;")
    row.addWidget(rule, 1)

    section.hide()
    outer.insertWidget(0, section)
    card._activity_section = section
    card._activity_section_label = label

    # A tiny hover surface helps each log row read as one object without turning
    # it back into a stack of cards.
    card.setStyleSheet(
        card.styleSheet()
        + "\nQFrame#activityCard:hover { background:#11141a; border:none; border-radius:5px; }"
    )


def _set_group(card: Any, category: str, *, start: bool, end: bool) -> None:
    _ensure_section_header(card)
    section = getattr(card, "_activity_section", None)
    label = getattr(card, "_activity_section_label", None)
    if section is None or label is None:
        return

    title, color = _SECTION_META.get(category, ("Activity", "#7f8999"))
    label.setText(title)
    label.setStyleSheet(
        "background:transparent; border:none; font-size:9px; font-weight:700; "
        f"letter-spacing:0.5px; color:{color}; padding:0; margin:0;"
    )
    section.setVisible(start)

    card.setProperty("activityCategory", category)
    card.setProperty("activityGroupStart", start)
    card.setProperty("activityGroupEnd", end)
    base.repolish(card)

    outer = card.layout()
    if outer is not None:
        # Same-category rows remain attached; only the end of a semantic block
        # earns a little breathing room before prose or a different action type.
        outer.setContentsMargins(0, 0, 0, 5 if end else 0)
        outer.setSpacing(1)
    card.updateGeometry()


def install() -> None:
    """Install semantic grouping once, after the density presentation pass."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_card_init = presentation.FlatActivityCard.__init__
    original_card_update = presentation.FlatActivityCard.update_card
    original_presented_title = presentation.FlatActivityCard._presented_title
    original_view_render = presentation.TranscriptView.render
    original_message_init = presentation.MessageWidget.__init__

    def card_init(self: Any, kind: str, parent: QWidget | None = None) -> None:
        original_card_init(self, kind, parent)
        _ensure_section_header(self)

    def presented_title(self: Any, title: str, status: str) -> str:
        value = original_presented_title(self, title, status)
        return _compact_process_title(value) if self.kind == "process" else value

    def card_update(
        self: Any,
        *,
        title: str,
        subtitle: str = "",
        status: str = "",
        body: str = "",
        auto_expand: bool | None = None,
    ) -> None:
        raw_title = title
        if self.kind == "tool":
            title = _friendly_tool_name(title)
        elif self.kind == "diff" and subtitle and "…" not in subtitle:
            paths = [part.strip() for part in subtitle.split(",") if part.strip()]
            if len(paths) == 1:
                title = f"Edited {_basename(paths[0])}"

        original_card_update(
            self,
            title=title,
            subtitle=subtitle,
            status=status,
            body=body,
            auto_expand=auto_expand,
        )

        # Category color is more useful than painting every completed command
        # green. Status remains available in expanded output and Runtime.
        if self.kind == "process":
            self.icon.set_tone("muted")
        elif self.kind in {"diff", "tool"}:
            self.icon.set_tone("accent")
        elif self.kind == "error":
            self.icon.set_tone("bad")

        if raw_title != title:
            self.title_label.setToolTip(raw_title)

    def view_render(self: Any, entries: list[TranscriptEntry]) -> None:
        # Runtime CardListView uses max_content_width=0 and must remain a literal
        # event list. The central transcript gets the editorial/coalesced view.
        if not self._max_content_width:
            original_view_render(self, entries)
            return

        visible = _coalesce_activity(entries)
        original_view_render(self, visible)

        for index, entry in enumerate(visible):
            category = _category(entry)
            if category is None:
                continue
            widget = self._widgets.get(entry.key)
            if not isinstance(widget, presentation.FlatActivityCard):
                continue
            previous = _category(visible[index - 1]) if index else None
            following = _category(visible[index + 1]) if index + 1 < len(visible) else None
            _set_group(
                widget,
                category,
                start=previous != category,
                end=following != category,
            )

    def message_init(self: Any, role: str, parent: QWidget | None = None) -> None:
        original_message_init(self, role, parent)
        if role != "assistant":
            return
        layout = self.layout()
        if layout is not None:
            # Prose/reasoning is a different semantic phase from execution rows.
            # Give it deliberate separation while action rows inside a group stay tight.
            layout.setContentsMargins(0, 8, 0, 10)

    presentation.FlatActivityCard.__init__ = card_init
    presentation.FlatActivityCard.update_card = card_update
    presentation.FlatActivityCard._presented_title = presented_title
    presentation.TranscriptView.render = view_render
    presentation.MessageWidget.__init__ = message_init


__all__ = ["install"]
