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

ACTIVITY_ACCENT: Final = "#818cf8"
CARD_BG: Final = "#0a0c10"

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
p {{ margin:0 0 11px; line-height:1.68; }}
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
body {{ font-family:{FONT_UI}; background:{BG_PANEL}; color:#b5bbc6; margin:8px 6px 18px; font-size:13px; }}
.event {{
    background:{CARD_BG};
    border:1px solid {BORDER};
    border-left:2px solid #292e42;
    padding:10px 11px 9px 10px;
    margin:0 0 7px;
}}
.marker {{
    display:inline-block; width:15px; font-family:{FONT_UI}; font-weight:800;
    margin-right:8px; font-size:10px;
}}
.good {{ color:{GOOD}; }}
.bad {{ color:{BAD}; }}
.accent {{ color:{ACCENT_SOFT}; }}
.warn {{ color:{WARN}; }}
.muted {{ color:{TEXT_MUTED}; }}
.summary {{ color:#d3d7df; font-family:{FONT_UI}; font-weight:600; line-height:1.4; font-size:12px; }}
.time {{
    color:{TEXT_FAINT}; font-family:{FONT_MONO}; font-size:10px;
    margin:5px 0 0 23px; letter-spacing:0.2px;
}}
.quiet {{ margin:60px 16px; color:#b0b6c0; font-size:15px; text-align:center; font-weight:500; }}
.quiet span {{ color:{TEXT_MUTED}; display:block; margin-top:8px; font-size:13px; font-weight:400; }}
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
    background:{ACCENT}; color:#ffffff; border-radius:9px;
    font-size:14px; font-weight:800;
}}
QLabel#brandLabel {{ color:{TEXT_STRONG}; font-size:15px; font-weight:650; letter-spacing:0.2px; }}
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
    border-radius:9px; margin:0; padding:0;
}}
QListWidget#threadList::item:hover {{ background:#12151c; border-color:#171b23; }}
QListWidget#threadList::item:selected {{ background:#171a26; border-color:#272b3d; }}
QWidget#threadItemWidget, QWidget#threadGroupHeader {{ background:transparent; }}
QFrame#threadRowMarker {{ background:transparent; border:none; border-radius:1px; }}
QFrame#threadRowMarker[active="true"] {{ background:{ACCENT}; }}
QLabel#threadItemTitle {{ color:#c3c9d4; font-size:13px; font-weight:500; }}
QLabel#threadItemTitle[active="true"] {{ color:{TEXT_STRONG}; font-weight:640; }}
QLabel#threadItemMeta {{ color:{TEXT_FAINT}; font-size:10px; }}
QLabel#threadItemMeta[state="running"] {{ color:#7fb2f5; }}
QLabel#threadItemMeta[state="waiting_approval"] {{ color:{WARN}; }}
QLabel#threadItemMeta[state="failed"] {{ color:{BAD}; }}
QLabel#threadGroupLabel {{
    color:{TEXT_FAINT}; font-size:10px; font-weight:700; letter-spacing:1.2px;
}}
QFrame#threadGroupRule {{ background:{BORDER}; border:none; }}
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
    background:#19182a; border:1px solid #302d4b; border-radius:16px;
}}
QFrame#userMessage:hover {{ background:#1d1b30; border-color:#3b3760; }}
QFrame#assistantMessage {{
    background:#0c0e13; border:1px solid #171c25; border-radius:16px;
}}
QFrame#assistantMessage:hover {{ background:#0d1016; border-color:#202631; }}
QLabel#messageRoleMark {{
    border-radius:12px; font-size:11px; font-weight:800;
    background:#171a22; color:#aeb4c0; border:1px solid #282e3a;
}}
QLabel#messageRoleMark[role="assistant"] {{
    background:#211e3a; color:#c5c0ff; border-color:#39335f;
}}
QLabel#messageRoleMark[role="user"] {{
    background:#202431; color:#d9dde7; border-color:#343a49;
}}
QLabel#messageRole {{
    color:{TEXT_MUTED}; font-size:12px; font-weight:650;
}}
QLabel#messageRole[role="assistant"] {{ color:{ACCENT_SOFT}; }}
QLabel#messageBody {{ background:transparent; color:{TEXT}; font-size:15px; }}
QLabel#streamBadge {{
    color:#aaa4f6; font-size:10px; font-weight:650;
    background:#171526; border-radius:8px; padding:3px 8px;
}}

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
    background:#0d1016;
    border:1px solid #202631;
    border-radius:14px;
}}
QFrame#activityCard:hover {{
    background:#0f1219;
    border-color:#2c3440;
}}
QFrame#activityCard[state="completed"] {{
    background:#0d1211;
    border-color:#25362f;
}}
QFrame#activityCard[state="running"], QFrame#activityCard[state="started"] {{
    background:#0d1118;
    border-color:#27364a;
}}
QFrame#activityCard[state="waiting"], QFrame#activityCard[state="waiting_approval"] {{
    background:#15120d;
    border-color:#4a3b22;
}}
QFrame#activityCard[state="failed"], QFrame#activityCard[state="denied"], QFrame#activityCard[state="cancelled"] {{
    background:#151013;
    border-color:#47282f;
}}
QLabel#cardIcon {{
    color:#c4cad4; font-size:13px; font-weight:800;
    background:#121720; border:1px solid #2b3440; border-radius:8px;
}}
QLabel#cardTitle {{
    color:#eef1f6; font-size:13px; font-weight:680;
}}
QLabel#cardSubtitle {{
    color:#747d8c; font-size:10px; font-family:{FONT_MONO};
}}
QFrame#cardBodyShell {{
    background:#090c11;
    border:1px solid #1d2530;
    border-radius:10px;
}}
QLabel#cardBodyTitle {{
    color:#8d96a5; font-size:10px; font-weight:700; letter-spacing:0.7px;
}}
QLabel#cardStatus {{
    font-size:10px; font-weight:700;
    color:#8b94a3;
    background:#11161d;
    border:1px solid #252d37;
    border-radius:8px;
    padding:3px 8px;
}}
QLabel#cardStatus[state="completed"] {{
    color:#8fd3b5;
    background:#0f1b17;
    border-color:#234538;
}}
QLabel#cardStatus[state="failed"], QLabel#cardStatus[state="denied"], QLabel#cardStatus[state="cancelled"] {{
    color:#e8a1aa;
    background:#211317;
    border-color:#4a2930;
}}
QLabel#cardStatus[state="waiting"], QLabel#cardStatus[state="waiting_approval"] {{
    color:#e8c07e;
    background:#211a10;
    border-color:#49391f;
}}
QLabel#cardStatus[state="running"], QLabel#cardStatus[state="started"] {{
    color:#9bcaff;
    background:#101925;
    border-color:#263c54;
}}
QPlainTextEdit#cardBody {{
    background:transparent;
    border:none;
    border-radius:0;
    color:#c2c8d1;
    font-family:{FONT_MONO};
    font-size:12px;
    padding:4px 1px 2px;
    selection-background-color:#38335b;
}}
QPushButton#cardToggle {{
    min-width:28px; max-width:28px; min-height:28px; max-height:28px;
    padding:0; border-radius:8px;
    background:#11161d; border:1px solid #252d37;
    color:#949dac; font-size:13px; font-weight:700;
}}
QPushButton#cardToggle:hover {{
    background:#171d26; border-color:#36404d; color:#eef1f6;
}}
QPushButton#cardToggle:pressed {{
    background:#0d1117; border-color:#2b333d;
}}

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

/* Workspace, permission and model are decisions, not decorations. */
QPushButton#composerControl {{
    min-height:27px; padding:0 10px; border-radius:8px;
    background:transparent; border:1px solid #1e2430;
    color:#9aa2b1; font-size:11px; font-weight:600;
}}
QPushButton#composerControl:hover {{ background:{BG_HOVER}; border-color:#2f3646; color:{TEXT}; }}
QPushButton#composerControl:disabled {{ color:#4d5462; border-color:#171b23; background:transparent; }}
QPushButton#composerControl[mode="full-access"] {{ color:{WARN}; border-color:#4a3a20; background:#1a1509; }}
QPushButton#composerControl[mode="read-only"] {{ color:#8ab4d8; border-color:#25384a; }}
QPushButton#composerControl[mode="workspace"] {{ color:#a9a2f0; border-color:#2e2b4a; }}

QLabel#menuCaption {{ color:{TEXT_FAINT}; font-size:11px; }}

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
QFrame#runtimeHeader {{
    background:#0d1016; border:1px solid #191e28; border-radius:12px;
    padding:14px 15px;
}}
QLabel#runtimeDot {{
    color:{ACTIVITY_ACCENT}; font-size:8px; background:{ACCENT_BG};
    border:1px solid #37325c; border-radius:7px; padding:1px 4px;
}}
QLabel#inspectorTitle {{ color:{TEXT_STRONG}; font-size:17px; font-weight:700; letter-spacing:0.2px; }}
QLabel#inspectorSubtitle {{ color:{TEXT_MUTED}; font-size:10px; letter-spacing:0.25px; }}
QTabWidget#activityTabs::pane {{
    background:transparent; border:none; border-top:1px solid {BORDER}; top:-1px;
}}
QTabWidget#activityTabs QTabBar {{ background:transparent; }}
QTabWidget#activityTabs QTabBar::tab {{
    background:transparent; color:{TEXT_MUTED}; padding:11px 8px 10px;
    margin-right:2px; min-width:0;
    border:none; border-bottom:2px solid transparent;
    font-family:{FONT_UI}; font-size:12px; font-weight:600;
}}
QTabWidget#activityTabs QTabBar::tab:hover {{ color:#c3c8d2; background:#0e1117; }}
QTabWidget#activityTabs QTabBar::tab:selected {{
    color:#e8e6ff; background:#10121a; border-bottom:2px solid {ACTIVITY_ACCENT};
}}
QLabel#panelPlaceholder {{
    color:{TEXT_FAINT}; font-size:32px; padding:50px 6px 8px;
    qproperty-alignment:AlignCenter;
}}
QLabel#panelPlaceholder span {{ color:{TEXT_MUTED}; font-size:13px; display:block; margin-top:8px; }}
QScrollArea#activityTimeline, QWidget#activityTimelineCanvas {{ background:{BG_PANEL}; border:none; }}
QFrame#activityEventRow {{
    background:{CARD_BG}; border:1px solid {BORDER}; border-radius:10px;
}}
QFrame#activityEventRow:hover {{ background:#0e1117; border-color:#272d38; }}
QLabel#activityEventTitle {{
    color:#d9dde5; font-family:{FONT_UI}; font-size:12px; font-weight:600;
}}
QLabel#activityEventTime {{ color:{TEXT_FAINT}; font-family:{FONT_MONO}; font-size:10px; }}
QLabel#panelPlaceholderTitle {{ color:#c9ced8; font-size:13px; font-weight:650; }}
QLabel#panelPlaceholderBody {{ color:{TEXT_MUTED}; font-size:11px; }}
QPlainTextEdit {{
    background:{BG_PANEL}; border:none; color:#adb3bf;
    padding:5px 2px 6px 3px; selection-background-color:#38335b;
    font-family:{FONT_MONO}; font-size:12px;
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
/* Styling any sub-control of a scrollbar drops the rest back to the native
   painter, which drew the trough as a light stripe down the dark sidebar. */
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
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
