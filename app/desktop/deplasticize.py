"""Final visual hierarchy pass for Loom Desktop.

The earlier polish modules solve individual controls.  This pass intentionally
removes the accumulated "UI kit" look that appears when every control gets its
own fill, outline, rounded container and accent colour.

Only presentation is changed here.  Behaviour, hit targets, runtime semantics,
streaming, menus and thread state remain owned by their existing modules.
"""

from __future__ import annotations

from app.desktop import theme


_INSTALLED = False

# This stylesheet is installed last on purpose.  It is a hierarchy pass over the
# component-specific polish layers: one primary accent, fewer visible borders,
# flatter toolbars, quieter metadata, and cards only where a surface genuinely
# needs containment.
_FLAT_CHROME_QSS = r"""
/* -------------------------------------------------------------------------
   Shell: panels are regions, not cards.
   ------------------------------------------------------------------------- */
QFrame#sidebar,
QFrame#activityPanel {
    background:#131419;
}
QFrame#sidebar {
    border-right:1px solid #1d1f26;
}
QFrame#activityPanel {
    border-left:1px solid #1d1f26;
}
QSplitter::handle {
    background:#1d1f26;
    width:1px;
}
QSplitter::handle:hover {
    background:#292c35;
}

/* -------------------------------------------------------------------------
   Sidebar chrome: actions read like a toolbar, not a row of plastic pills.
   ------------------------------------------------------------------------- */
QPushButton#newThreadButton,
QPushButton#openProjectButton,
QPushButton#archiveViewButton,
QPushButton#threadActionsButton {
    background:transparent;
    border:1px solid transparent;
    border-radius:7px;
    color:#aeb4bf;
}
QPushButton#newThreadButton {
    color:#e4e6eb;
    font-weight:650;
}
QPushButton#newThreadButton:hover,
QPushButton#openProjectButton:hover,
QPushButton#archiveViewButton:hover,
QPushButton#threadActionsButton:hover {
    background:#1c1e25;
    border-color:transparent;
    color:#f0f1f4;
}
QPushButton#newThreadButton:pressed,
QPushButton#openProjectButton:pressed,
QPushButton#archiveViewButton:pressed,
QPushButton#threadActionsButton:pressed {
    background:#181a20;
}
QPushButton#archiveViewButton:checked {
    background:#202129;
    border-color:transparent;
    color:#e2e4ea;
}
QLineEdit#threadSearch {
    background:#191a20;
    border:1px solid transparent;
    border-radius:7px;
    color:#d4d7de;
}
QLineEdit#threadSearch:hover {
    background:#1d1f26;
    border-color:transparent;
}
QLineEdit#threadSearch:focus {
    background:#1f2027;
    border-color:#343741;
}

/* -------------------------------------------------------------------------
   Conversation header: status is typography, not another badge collection.
   ------------------------------------------------------------------------- */
QFrame#workspaceHeader {
    border:none;
    border-bottom:1px solid #1d1f26;
}
QPushButton#panelToggle {
    background:transparent;
    border:1px solid transparent;
    border-radius:6px;
    color:#8e949f;
}
QPushButton#panelToggle:hover {
    background:#1b1d23;
    border-color:transparent;
    color:#c7cbd2;
}
QPushButton#panelToggle[active="true"] {
    background:transparent;
    border-color:transparent;
    color:#c2bdf2;
}
QLabel#statusChip,
QLabel#sandboxChip {
    background:transparent;
    border:none;
    border-radius:0;
    padding:2px 3px;
    color:#858c99;
    font-size:10px;
    font-weight:650;
}
QLabel#statusChip[state="running"],
QLabel#statusChip[state="starting"] { color:#8db5df; }
QLabel#statusChip[state="completed"] { color:#78b394; }
QLabel#statusChip[state="waiting_approval"] { color:#cfad70; }
QLabel#statusChip[state="failed"],
QLabel#statusChip[state="cancelled"] { color:#ce848e; }
QLabel#sandboxChip[state="enforced"] { color:#78b394; }
QLabel#sandboxChip[state="unprotected"] { color:#ce848e; }

/* -------------------------------------------------------------------------
   Messages: keep the user bubble, but remove the shiny bordered capsule feel.
   Assistant output already remains flat in output_presentation.
   ------------------------------------------------------------------------- */
QFrame#userMessage {
    background:#25222e;
    border:1px solid transparent;
    border-radius:12px;
}
QFrame#userMessage:hover {
    background:#292631;
    border-color:transparent;
}

/* -------------------------------------------------------------------------
   Composer: one containing surface, one selector, one primary action.
   Everything else behaves like a toolbar affordance.
   ------------------------------------------------------------------------- */
QFrame#composerFrame,
QFrame#composerFrame:hover {
    background:#18191f;
    border:1px solid #2a2c34;
    border-radius:14px;
}
QFrame#composerFrame[focused="true"] {
    background:#191a20;
    border-color:#3d4049;
}
QTextEdit#composer {
    color:#e9ebf0;
    background:transparent;
}

QPushButton#composerControl,
QPushButton#composerAttach,
QPushButton#composerWorkspace,
QPushButton#composerPermission {
    min-height:29px;
    max-height:29px;
    padding:0 8px;
    background:transparent;
    border:1px solid transparent;
    border-radius:7px;
    color:#aeb4bf;
    font-size:11px;
    font-weight:580;
}
QPushButton#composerControl:hover,
QPushButton#composerAttach:hover,
QPushButton#composerWorkspace:hover,
QPushButton#composerPermission:hover {
    background:#22232a;
    border-color:transparent;
    color:#eceef2;
}
QPushButton#composerControl:pressed,
QPushButton#composerAttach:pressed,
QPushButton#composerWorkspace:pressed,
QPushButton#composerPermission:pressed {
    background:#1d1e24;
    border-color:transparent;
}
QPushButton#composerControl:disabled,
QPushButton#composerAttach:disabled,
QPushButton#composerWorkspace:disabled,
QPushButton#composerPermission:disabled {
    background:transparent;
    border-color:transparent;
    color:#555b65;
}

/* Permission stays semantic, but colour no longer creates another physical pill. */
QPushButton#composerPermission[mode="full-access"] {
    background:transparent;
    border-color:transparent;
    color:#d6b86f;
}
QPushButton#composerPermission[mode="full-access"]:hover {
    background:#242117;
    border-color:transparent;
    color:#e5c77d;
}
QPushButton#composerPermission[mode="read-only"] {
    background:transparent;
    border-color:transparent;
    color:#92b3cb;
}
QPushButton#composerPermission[mode="workspace"] {
    background:transparent;
    border-color:transparent;
    color:#aaa4d5;
}
QPushButton#composerPermission[mode="approval"] {
    background:transparent;
    border-color:transparent;
    color:#b3afbb;
}

/* The model is a real selector, so it is the one secondary control allowed a
   persistent surface.  Keep it neutral rather than violet. */
QPushButton#composerModel {
    min-height:28px;
    max-height:28px;
    padding:0 27px 0 11px;
    background:#292a2f;
    border:1px solid transparent;
    border-radius:14px;
    color:#e7e8eb;
    font-size:11px;
    font-weight:590;
}
QPushButton#composerModel:hover {
    background:#303136;
    border-color:transparent;
    color:#ffffff;
}
QPushButton#composerModel:pressed {
    background:#25262b;
    border-color:transparent;
}
QPushButton#composerModel:disabled {
    background:#222329;
    border-color:transparent;
    color:#6d7077;
}

/* Usage is metadata.  The native context-ring may remain, but the container
   itself disappears so this no longer reads like a fifth button. */
QLabel#composerUsage,
QLabel#composerUsage:hover {
    min-height:25px;
    max-height:25px;
    background:transparent;
    border:none;
    border-radius:0;
    color:#7f8793;
    font-size:10px;
    font-weight:560;
}

/* Send is the only strong action in the composer.  A flat fill avoids the
   glossy gradient that was responsible for much of the plastic appearance. */
QPushButton#sendButton {
    background:#7569df;
    border:1px solid transparent;
    border-radius:9px;
    color:#ffffff;
}
QPushButton#sendButton:hover {
    background:#8176e7;
    border-color:transparent;
}
QPushButton#sendButton:pressed {
    background:#685dd1;
    border-color:transparent;
}
QPushButton#sendButton:disabled {
    background:#23242b;
    border-color:transparent;
    color:#626873;
}

/* -------------------------------------------------------------------------
   Runtime inspector: remove the card-inside-a-panel effect.
   ------------------------------------------------------------------------- */
QFrame#runtimeHeader {
    background:transparent;
    border:none;
    border-radius:0;
    padding:1px 0 5px 0;
}
QLabel#runtimeDot {
    background:transparent;
    border:none;
    border-radius:0;
    padding:0;
}
QLabel#inspectorTitle {
    color:#eceef2;
    font-size:16px;
    font-weight:670;
}
QLabel#inspectorSubtitle {
    color:#747b87;
}
QTabWidget#activityTabs::pane {
    background:transparent;
    border:none;
    border-top:1px solid #1d1f26;
    top:-1px;
}
QTabWidget#activityTabs QTabBar::tab {
    background:transparent;
    border:none;
    border-bottom:1px solid transparent;
    color:#858c98;
    padding:10px 8px 9px;
    font-weight:580;
}
QTabWidget#activityTabs QTabBar::tab:hover {
    background:transparent;
    color:#c2c6ce;
}
QTabWidget#activityTabs QTabBar::tab:selected {
    background:transparent;
    color:#e3e5e9;
    border-bottom:1px solid #7771d5;
}
QFrame#activityEventRow {
    background:transparent;
    border:1px solid transparent;
    border-radius:7px;
}
QFrame#activityEventRow:hover {
    background:#191b21;
    border-color:transparent;
}

/* -------------------------------------------------------------------------
   Secondary surfaces: reserve visible outlines for genuinely actionable or
   exceptional content instead of every decorative container.
   ------------------------------------------------------------------------- */
QFrame#approvalCard {
    background:#1d1b26;
    border:1px solid #373240;
    border-radius:10px;
}
QFrame#banner {
    border-radius:8px;
}

/* Quiet scrollbars recede until the user actually reaches for them. */
QScrollBar:vertical {
    width:7px;
    margin:2px 1px;
    background:transparent;
}
QScrollBar::handle:vertical {
    min-height:30px;
    background:#282b33;
    border-radius:3px;
}
QScrollBar::handle:vertical:hover {
    background:#3a3e48;
}
QScrollBar:horizontal {
    height:7px;
    margin:1px 2px;
    background:transparent;
}
QScrollBar::handle:horizontal {
    min-width:30px;
    background:#282b33;
    border-radius:3px;
}
"""


def install() -> None:
    """Append the global hierarchy stylesheet after all component polish."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_stylesheet = theme.stylesheet

    def stylesheet() -> str:
        return original_stylesheet() + _FLAT_CHROME_QSS

    theme.stylesheet = stylesheet


__all__ = ["install"]
