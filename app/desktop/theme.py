"""Design tokens and the single stylesheet for the desktop client.

Previously the window's appearance was assembled by five layers each appending
to ``styleSheet()``, so the effective rule for any widget depended on subclass
order. Everything lives here now.
"""

from __future__ import annotations

import os
from typing import Final


# ---- tokens --------------------------------------------------------------

BG_APP: Final = "#090a0e"
BG_PANEL: Final = "#0b0d12"
BG_RAISED: Final = "#0e1117"
BG_INPUT: Final = "#10141b"
BG_HOVER: Final = "#141820"

BORDER: Final = "#181c24"
BORDER_STRONG: Final = "#252b36"
BORDER_FOCUS: Final = "#4a4670"

TEXT: Final = "#e8ebf1"
TEXT_STRONG: Final = "#f5f6f9"
TEXT_MUTED: Final = "#79808f"
TEXT_FAINT: Final = "#5e6675"

ACCENT: Final = "#6f65df"
ACCENT_SOFT: Final = "#9189f0"
ACCENT_BG: Final = "#171624"

GOOD: Final = "#74bd9d"
WARN: Final = "#e0b473"
BAD: Final = "#df8e98"

FONT_UI: Final = (
    '"Segoe UI Variable Text","Segoe UI Variable","Microsoft YaHei UI",'
    '"Microsoft YaHei","Segoe UI",sans-serif'
)
FONT_MONO: Final = (
    '"Cascadia Mono","Cascadia Code","Consolas","Microsoft YaHei UI",monospace'
)

# ---- motion --------------------------------------------------------------

MOTION_MICRO_MS: Final = 80
MOTION_FAST_MS: Final = 120
MOTION_BASE_MS: Final = 160
MOTION_CONTENT_MS: Final = 190
MOTION_PANEL_MS: Final = 230


def motion_enabled() -> bool:
    """Honour LOOM_REDUCE_MOTION, matching the previous client's contract."""
    return os.getenv("LOOM_REDUCE_MOTION", "").strip().casefold() not in {
        "1",
        "true",
        "yes",
        "on",
    }


# ---- rich-text stylesheets ----------------------------------------------

MESSAGE_CSS: Final = f"""
<style>
body {{ color:{TEXT}; font-family:{FONT_UI}; font-size:15px; }}
p {{ margin:0 0 10px; line-height:1.62; }}
strong {{ color:{TEXT_STRONG}; }}
em {{ color:{TEXT}; }}
a {{ color:{ACCENT_SOFT}; }}
code {{ background:#151923; color:#d8d4ff; font-family:{FONT_MONO}; font-size:13px; }}
.h1,.h2,.h3 {{ color:{TEXT_STRONG}; font-weight:700; margin:14px 0 8px; }}
.h1 {{ font-size:20px; }}
.h2 {{ font-size:17px; }}
.h3 {{ font-size:15px; }}
ul,ol {{ margin:4px 0 11px 20px; }}
li {{ margin:0 0 4px; line-height:1.55; }}
.check {{ font-weight:800; }}
.check.done {{ color:{GOOD}; }}
.check.todo {{ color:{TEXT_MUTED}; }}
blockquote {{
    color:#b3b9c4; border-left:2px solid #413d5c;
    margin:8px 0 11px; padding:3px 0 3px 12px;
}}
.rule {{ border-top:1px solid {BORDER_STRONG}; margin:12px 0; }}
</style>
"""

ACTIVITY_CSS: Final = f"""
<style>
body {{ font-family:{FONT_UI}; background:{BG_PANEL}; color:#b5bbc6; margin:4px 6px 16px; font-size:12px; }}
.event {{ border-bottom:1px solid {BORDER}; padding:10px 3px; }}
.marker {{ display:inline-block; width:19px; font-weight:800; margin-right:7px; }}
.good {{ color:{GOOD}; }}
.bad {{ color:{BAD}; }}
.accent {{ color:#8a82e8; }}
.muted {{ color:{TEXT_MUTED}; }}
.summary {{ color:#cbd0d8; line-height:1.45; }}
.time {{ color:{TEXT_FAINT}; font-family:{FONT_MONO}; font-size:9px; margin:4px 0 0 26px; }}
.quiet {{ margin:48px 10px; color:#d4d7dd; font-size:13px; }}
.quiet span {{ color:{TEXT_MUTED}; }}
</style>
"""


def stylesheet() -> str:
    """The complete application stylesheet."""
    return f"""
QMainWindow, QWidget {{
    background:{BG_APP};
    color:{TEXT};
    font-family:{FONT_UI};
    font-size:13px;
}}
/* QLabel is a QWidget, so the rule above would paint the app background as a
   visible dark rectangle on every panel that is lighter than it. */
QLabel {{ background:transparent; }}
QFrame#sidebar {{ background:{BG_PANEL}; border-right:1px solid {BORDER}; }}
QFrame#activityPanel {{ background:{BG_PANEL}; border-left:1px solid {BORDER}; }}
QFrame#conversationPanel {{ background:{BG_APP}; }}

/* ---- brand / sidebar ---- */
QLabel#brandMark {{
    background:{ACCENT}; color:#ffffff; border-radius:10px;
    font-size:16px; font-weight:800;
}}
QLabel#brandLabel {{ color:{TEXT_STRONG}; font-size:19px; font-weight:700; }}
QLabel#brandSubtitle, QLabel#mutedLabel {{ color:{TEXT_MUTED}; font-size:11px; }}
QLabel#sectionLabel {{
    color:{TEXT_MUTED}; font-size:10px; font-weight:700; letter-spacing:1.35px;
}}

/* ---- buttons ---- */
QPushButton {{
    min-height:34px;
    background:#13161d;
    border:1px solid {BORDER_STRONG};
    border-radius:9px;
    padding:1px 12px;
    color:#ccd0da;
    font-size:13px;
    font-weight:560;
}}
QPushButton:hover {{ background:#181c25; border-color:#333a49; color:{TEXT_STRONG}; }}
QPushButton:pressed {{ background:#10131a; }}
QPushButton:disabled {{ color:#4a5060; background:#0e1015; border-color:#171b23; }}
QPushButton#newThreadButton, QPushButton#sendButton, QPushButton#allowButton {{
    background:{ACCENT}; border-color:#7a70e6; color:#ffffff; font-weight:700;
}}
QPushButton#newThreadButton:hover, QPushButton#sendButton:hover, QPushButton#allowButton:hover {{
    background:#7c72ea;
}}
QPushButton#newThreadButton:disabled, QPushButton#sendButton:disabled {{
    background:#22203a; border-color:#2b2846; color:#6f6a92;
}}
QPushButton#openProjectButton {{ min-width:58px; }}
QPushButton#denyButton, QPushButton#stopButton {{ background:#12151b; }}
QPushButton#stopButton:enabled {{ color:{BAD}; border-color:#45282d; }}
QPushButton#iconButton {{
    min-width:30px; max-width:30px; min-height:30px; max-height:30px;
    padding:0; border-radius:8px; background:transparent; border-color:transparent;
    font-size:13px;
}}
QPushButton#iconButton:hover {{ background:{BG_HOVER}; border-color:{BORDER_STRONG}; }}
QPushButton#panelToggle {{
    min-height:27px; padding:0 9px; background:transparent;
    border-color:#1a1e27; color:{TEXT_MUTED}; font-size:10px;
}}
QPushButton#panelToggle:hover {{ color:#c6cad4; border-color:#2b3140; }}
QPushButton#panelToggle[active="true"] {{ color:#aaa2f6; border-color:#302d4b; }}

/* ---- conversation library ---- */
QFrame#threadLibraryToolbar {{ background:transparent; border:none; }}
QLineEdit#threadSearch {{
    min-height:34px; background:{BG_RAISED};
    border:1px solid #1c222c; border-radius:9px; padding:0 10px;
    color:#d9dde5; selection-background-color:#5f57c9; font-size:12px;
}}
QLineEdit#threadSearch:hover {{ border-color:#29303c; background:{BG_INPUT}; }}
QLineEdit#threadSearch:focus {{ border-color:{BORDER_FOCUS}; background:#11151d; }}
QPushButton#archiveViewButton {{
    min-height:34px; padding:0 10px; background:{BG_RAISED};
    border:1px solid #1c222c; border-radius:9px;
    color:{TEXT_MUTED}; font-size:10px; font-weight:650;
}}
QPushButton#archiveViewButton:hover {{ color:#c9ced8; border-color:#2b3240; background:#12161d; }}
QPushButton#archiveViewButton:checked {{
    color:#d9d5ff; background:{ACCENT_BG}; border-color:#3b385d;
}}
QPushButton#threadActionsButton {{
    min-height:34px; max-height:34px; padding:0; background:{BG_RAISED};
    border:1px solid #1c222c; border-radius:9px; color:{TEXT_MUTED}; font-weight:800;
}}
QPushButton#threadActionsButton:hover {{ color:{TEXT_STRONG}; background:{BG_HOVER}; border-color:#303744; }}
QPushButton#threadActionsButton:disabled {{ color:#343b48; background:#0c0e13; border-color:#151920; }}

QListWidget#threadList {{ background:transparent; border:none; outline:none; padding:0; }}
QListWidget#threadList::item {{
    background:transparent; border:1px solid transparent;
    border-radius:8px; margin:0; padding:0;
}}
QListWidget#threadList::item:hover {{ background:#12151c; }}
QListWidget#threadList::item:selected {{ background:#1a1d29; border-color:#2b2f40; }}
QWidget#threadItemWidget, QWidget#threadGroupHeader {{ background:transparent; }}
QLabel#threadItemTitle {{ color:#cfd4dd; font-size:13px; font-weight:500; }}
QLabel#threadGroupLabel {{
    color:{TEXT_FAINT}; font-size:10px; font-weight:700; letter-spacing:1.2px;
}}
QLabel#threadDot {{ font-size:9px; color:{TEXT_MUTED}; }}
QLabel#threadDot[state="running"] {{ color:#7fb2f5; }}
QLabel#threadDot[state="waiting_approval"] {{ color:{WARN}; }}
QLabel#threadDot[state="failed"] {{ color:{BAD}; }}

/* ---- conversation header ---- */
QFrame#workspaceHeader {{ background:transparent; border:none; border-bottom:1px solid {BORDER}; }}
QLabel#threadTitle {{ color:{TEXT_STRONG}; font-size:19px; font-weight:700; }}
QLabel#projectName {{ color:#a7adbb; font-size:11px; font-weight:650; }}
QLabel#statusChip, QLabel#sandboxChip {{
    border-radius:9px; padding:4px 10px; font-size:10px; font-weight:650;
    background:#11141b; color:#9299aa;
}}
QLabel#statusChip[state="running"], QLabel#statusChip[state="starting"] {{ background:#121a25; color:#9ac8ff; }}
QLabel#statusChip[state="completed"] {{ background:#101a17; color:{GOOD}; }}
QLabel#statusChip[state="waiting_approval"] {{ background:#211a10; color:{WARN}; }}
QLabel#statusChip[state="failed"], QLabel#statusChip[state="cancelled"] {{ background:#221416; color:{BAD}; }}
QLabel#sandboxChip[state="enforced"] {{ background:#101a17; color:{GOOD}; }}
QLabel#sandboxChip[state="unprotected"] {{ background:#211517; color:{BAD}; }}
QLabel#connectionDot {{ color:{GOOD}; background:transparent; font-size:9px; }}
QLabel#connectionDot[state="disconnected"] {{ color:{BAD}; }}
QLabel#protocolLabel {{ color:{TEXT_MUTED}; background:transparent; font-size:10px; }}

/* ---- transcript ---- */
QScrollArea#transcript {{ background:{BG_APP}; border:none; }}
QWidget#transcriptCanvas {{ background:{BG_APP}; }}

QFrame#userMessage {{
    background:#11141b; border:1px solid {BORDER_STRONG}; border-radius:12px;
}}
QFrame#assistantMessage {{ background:transparent; border:none; }}
QLabel#messageRole {{
    color:{TEXT_MUTED}; font-size:10px; font-weight:700; letter-spacing:1.05px;
}}
QLabel#messageRole[role="assistant"] {{ color:{ACCENT_SOFT}; }}
QLabel#messageBody {{ background:transparent; color:{TEXT}; font-size:15px; }}
QLabel#streamBadge {{ color:#aaa4f6; font-size:9px; font-weight:650; }}

QFrame#codeBlock {{ background:#0c0f14; border:1px solid #202631; border-radius:9px; }}
QLabel#codeLanguage {{ color:{TEXT_MUTED}; font-family:{FONT_MONO}; font-size:10px; }}
QPlainTextEdit#codeBody {{
    background:transparent; border:none; color:#ccd1da;
    font-family:{FONT_MONO}; font-size:12px; padding:0;
    selection-background-color:#38335b;
}}
QPushButton#copyButton {{
    min-height:22px; max-height:22px; padding:0 9px; border-radius:6px;
    background:transparent; border:1px solid transparent;
    color:{TEXT_MUTED}; font-size:10px; font-weight:600;
}}
QPushButton#copyButton:hover {{ background:{BG_HOVER}; border-color:{BORDER_STRONG}; color:{TEXT}; }}

/* ---- inline activity cards ---- */
QFrame#activityCard {{
    background:#0c0e14; border:1px solid #1b202a; border-radius:10px;
}}
QFrame#activityCard[state="failed"], QFrame#activityCard[state="denied"] {{ border-color:#432a2f; }}
QFrame#activityCard[state="waiting"], QFrame#activityCard[state="waiting_approval"] {{ border-color:#453720; }}
QFrame#activityCard[state="running"], QFrame#activityCard[state="started"] {{ border-color:#2a3350; }}
QLabel#cardIcon {{ color:{TEXT_MUTED}; font-size:12px; font-weight:800; }}
QLabel#cardTitle {{ color:#d6dae2; font-size:12px; font-weight:650; }}
QLabel#cardSubtitle {{ color:{TEXT_MUTED}; font-size:11px; font-family:{FONT_MONO}; }}
QLabel#cardStatus {{ font-size:10px; font-weight:650; color:{TEXT_MUTED}; }}
QLabel#cardStatus[state="completed"] {{ color:{GOOD}; }}
QLabel#cardStatus[state="failed"], QLabel#cardStatus[state="denied"] {{ color:{BAD}; }}
QLabel#cardStatus[state="waiting"], QLabel#cardStatus[state="waiting_approval"] {{ color:{WARN}; }}
QLabel#cardStatus[state="running"], QLabel#cardStatus[state="started"] {{ color:#95c4fb; }}
QPlainTextEdit#cardBody {{
    background:#080a0e; border:1px solid #171b23; border-radius:7px;
    color:#aeb4c0; font-family:{FONT_MONO}; font-size:11px; padding:7px 8px;
    selection-background-color:#38335b;
}}
QPushButton#cardToggle {{
    min-height:24px; padding:0 8px; border-radius:6px;
    background:transparent; border:1px solid transparent;
    color:{TEXT_MUTED}; font-size:10px; font-weight:600;
}}
QPushButton#cardToggle:hover {{ background:{BG_HOVER}; border-color:{BORDER_STRONG}; color:{TEXT}; }}

/* ---- empty state ---- */
QFrame#emptyState, QFrame#emptyStateContent {{ background:transparent; }}
QLabel#emptyKicker {{ color:#8175e8; font-size:10px; font-weight:750; letter-spacing:1.5px; }}
QLabel#emptyTitle {{ color:{TEXT_STRONG}; font-size:25px; font-weight:700; }}
QLabel#emptyBody {{ color:{TEXT_MUTED}; font-size:13px; }}
QPushButton#promptSuggestion {{
    background:{BG_RAISED}; border:1px solid #1a1f2a; border-radius:10px;
    min-height:40px; color:#adb3c0; font-weight:550; text-align:left; padding:0 13px;
}}
QPushButton#promptSuggestion:hover {{ background:#11151d; border-color:#2a3040; color:{TEXT}; }}

/* ---- composer ---- */
QFrame#composerFrame {{
    background:{BG_RAISED}; border:1px solid {BORDER_STRONG}; border-radius:14px;
}}
QFrame#composerFrame[focused="true"] {{ border-color:{BORDER_FOCUS}; background:#11151d; }}
QTextEdit#composer {{
    background:transparent; border:none; padding:4px 2px 6px;
    color:{TEXT_STRONG}; font-size:14px; selection-background-color:#484078;
}}
QLabel#composerHint, QLabel#composerState {{ color:{TEXT_FAINT}; font-size:10px; }}
QLabel#composerState {{ color:#8990a0; }}

/* Workspace and permission are decisions, not decorations. */
QPushButton#composerControl {{
    min-height:26px; padding:0 10px; border-radius:8px;
    background:transparent; border:1px solid #1e2430;
    color:#9aa2b1; font-size:11px; font-weight:600;
}}
QPushButton#composerControl:hover {{ background:{BG_HOVER}; border-color:#2f3646; color:{TEXT}; }}
QPushButton#composerControl[mode="full-access"] {{ color:{WARN}; border-color:#4a3a20; }}
QPushButton#composerControl[mode="read-only"] {{ color:#8ab4d8; border-color:#25384a; }}

QPushButton#sendButton {{
    border-radius:17px; padding:0; font-size:15px; font-weight:800;
}}

/* ---- approval ---- */
QFrame#approvalCard {{ background:{ACCENT_BG}; border:1px solid #40365d; border-radius:13px; }}
QLabel#approvalIcon {{ background:{ACCENT}; color:#ffffff; border-radius:12px; font-weight:800; }}
QLabel#approvalTitle {{ color:#eeeaff; font-size:13px; font-weight:700; }}
QLabel#approvalDetails {{ color:#b6b1c3; font-size:12px; }}
QPlainTextEdit#approvalArguments {{
    background:#0d0f16; border:1px solid #2b2740; border-radius:8px;
    color:#b9b4cb; font-family:{FONT_MONO}; font-size:11px; padding:7px 8px;
}}

/* ---- inline error banner ---- */
QFrame#banner {{ background:#1d1417; border:1px solid #45282d; border-radius:10px; }}
QLabel#bannerText {{ color:#f0c3c7; font-size:11px; }}
QPushButton#bannerClose {{
    min-height:22px; max-height:22px; min-width:22px; max-width:22px;
    padding:0; border-radius:6px; background:transparent;
    border-color:transparent; color:#c9959c; font-weight:800;
}}
QPushButton#bannerClose:hover {{ background:#2a1a1e; color:#ffdde0; }}

/* ---- runtime inspector ---- */
QLabel#inspectorTitle {{ color:{TEXT_STRONG}; font-size:16px; font-weight:700; }}
QTabWidget#activityTabs::pane {{ background:transparent; border:none; top:-1px; }}
QTabBar::tab {{
    background:transparent; color:{TEXT_MUTED}; padding:9px 5px;
    margin-right:3px;
    border:none; border-bottom:2px solid transparent; font-size:11px;
}}
QTabBar::tab:hover {{ color:#b5bac5; }}
QTabBar::tab:selected {{ color:#e7e5ff; border-bottom-color:{ACCENT}; }}
QLabel#panelPlaceholder {{
    color:{TEXT_MUTED}; font-size:12px; padding:44px 6px 0;
}}
QLabel#panelPlaceholder span {{ color:{TEXT_FAINT}; }}
QTextBrowser#activityView, QPlainTextEdit {{
    background:{BG_PANEL}; border:none; color:#adb3bf;
    padding:6px 2px 6px 4px; selection-background-color:#38335b;
    font-family:{FONT_MONO}; font-size:11px;
}}

/* ---- menus and chrome ---- */
QMenu {{
    background:#101319; border:1px solid #282e39; border-radius:9px;
    padding:6px; color:#d5d9e1; font-family:{FONT_UI}; font-size:12px;
}}
QMenu::item {{ min-width:150px; padding:8px 17px 8px 11px; border-radius:6px; }}
QMenu::item:selected {{ background:#1b2029; color:#ffffff; }}
QMenu::separator {{ height:1px; background:#232934; margin:5px 7px; }}

QSplitter::handle {{ background:{BORDER}; width:1px; }}
QSplitter::handle:hover {{ background:#323746; }}
QScrollBar:vertical {{ background:transparent; width:9px; margin:2px; }}
QScrollBar::handle:vertical {{ background:#292e39; border-radius:4px; min-height:30px; }}
QScrollBar::handle:vertical:hover {{ background:#3a4150; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar:horizontal {{ background:transparent; height:9px; margin:2px; }}
QScrollBar::handle:horizontal {{ background:#292e39; border-radius:4px; min-width:30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width:0; }}
"""


__all__ = [
    "ACTIVITY_CSS",
    "MESSAGE_CSS",
    "MOTION_BASE_MS",
    "MOTION_CONTENT_MS",
    "MOTION_FAST_MS",
    "MOTION_MICRO_MS",
    "MOTION_PANEL_MS",
    "motion_enabled",
    "stylesheet",
]
