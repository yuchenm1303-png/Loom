"""Final reading-surface polish for the central conversation.

This pass deliberately targets the places that still made the transcript feel
like a component demo: heavy Markdown defaults, bright tool rows, nested output
cards, and a footer whose controls competed with the answer itself.

It does not change Runtime semantics or the disclosure architecture.  It only
refines typography, density and local chrome after the existing presentation
modules have installed their behavior.
"""

from __future__ import annotations

from typing import Any

from app.desktop import message_actions, output_presentation, theme


_INSTALLED = False


_ASSISTANT_DOCUMENT_CSS = f"""
<style>
body {{
    color:#dde0e6;
    font-family:{theme.FONT_UI};
    font-size:14px;
}}
p {{
    margin:0 0 8px;
    line-height:1.56;
}}
strong {{
    color:#f0f1f4;
    font-weight:600;
}}
em {{ color:#d8dbe1; }}
a {{ color:#a9a4dd; text-decoration:none; }}
code {{
    background:#17191f;
    color:#d0d3da;
    font-family:{theme.FONT_MONO};
    font-size:12px;
}}
.h1,.h2,.h3 {{
    color:#f0f1f4;
    font-weight:650;
    margin:12px 0 6px;
}}
.h1 {{ font-size:18px; }}
.h2 {{ font-size:16px; }}
.h3 {{ font-size:14px; }}
ul,ol {{
    margin:3px 0 9px 18px;
}}
li {{
    margin:0 0 3px;
    line-height:1.48;
}}
.check {{ font-weight:700; }}
.check.done {{ color:#78aa91; }}
.check.todo {{ color:#858c97; }}
blockquote {{
    color:#aeb3bc;
    border-left:1px solid #3a3d46;
    margin:7px 0 9px;
    padding:2px 0 2px 10px;
}}
.rule {{
    border-top:1px solid #24272e;
    margin:10px 0;
}}
.mdTable {{
    margin:4px 0 10px;
    border-collapse:collapse;
}}
.mdTable th {{
    color:#9aa1ac;
    font-weight:600;
    font-size:12px;
    padding:5px 9px 6px 0;
    border-bottom:1px solid #30333b;
}}
.mdTable td {{
    color:#d8dbe1;
    padding:6px 9px 6px 0;
    border-bottom:1px solid #20232a;
}}
.callout {{
    color:#c9cdd4;
    background:#17181c;
    border-left:2px solid #8d7b53;
    margin:6px 0 9px;
    padding:6px 10px;
}}
.callout strong {{
    color:#e6e0d1;
    font-weight:600;
}}
</style>
"""


_ACTIVITY_LOCAL_QSS = f"""
QFrame#activityCard,
QFrame#activityCard:hover,
QFrame#activityCard[state="completed"],
QFrame#activityCard[state="running"],
QFrame#activityCard[state="started"],
QFrame#activityCard[state="waiting"],
QFrame#activityCard[state="waiting_approval"],
QFrame#activityCard[state="failed"],
QFrame#activityCard[state="denied"],
QFrame#activityCard[state="cancelled"] {{
    background:transparent;
    border:none;
    border-radius:0;
}}
QLabel#cardTitle {{
    background:transparent;
    color:#c9cdd4;
    font-size:12px;
    font-weight:570;
}}
QLabel#cardSubtitle {{
    background:transparent;
    color:#727985;
    font-family:{theme.FONT_MONO};
    font-size:10px;
    padding:0;
}}
QFrame#cardBodyShell {{
    background:#15171c;
    border:1px solid #24272f;
    border-radius:7px;
}}
QFrame#cardBodyShell[state="running"],
QFrame#cardBodyShell[state="started"] {{ border-color:#29313a; }}
QFrame#cardBodyShell[state="failed"],
QFrame#cardBodyShell[state="denied"],
QFrame#cardBodyShell[state="cancelled"] {{ border-color:#3b292d; }}
QFrame#cardBodyShell[state="waiting"],
QFrame#cardBodyShell[state="waiting_approval"] {{ border-color:#3b3326; }}
QLabel#cardBodyTitle {{
    background:transparent;
    border:none;
    color:#7c838d;
    font-size:10px;
    font-weight:570;
    letter-spacing:0.25px;
}}
QPlainTextEdit#cardBody {{
    background:transparent;
    border:none;
    color:#b8bec7;
    font-family:{theme.FONT_MONO};
    font-size:11px;
    padding:1px 0 0 0;
    selection-background-color:#343842;
}}
QLabel#cardStatus {{
    background:transparent;
    border:none;
    border-radius:0;
    padding:0;
    color:#858c96;
    font-size:10px;
    font-weight:570;
}}
QLabel#cardStatus[state="failed"],
QLabel#cardStatus[state="denied"],
QLabel#cardStatus[state="cancelled"] {{ color:#c77f88; }}
QLabel#cardStatus[state="waiting"],
QLabel#cardStatus[state="waiting_approval"] {{ color:#b99a66; }}
"""


_REASONING_LOCAL_QSS = f"""
QFrame#reasoningBlock {{
    background:transparent;
    border:none;
}}
QPushButton#reasoningToggle {{
    background:transparent;
    border:none;
    border-radius:5px;
    padding:1px 6px 1px 21px;
    color:#747c88;
    font-family:{theme.FONT_UI};
    font-size:11px;
    font-weight:520;
    text-align:left;
    min-height:20px;
}}
QPushButton#reasoningToggle:hover {{
    background:#15171c;
    color:#aab0b9;
}}
QPushButton#reasoningToggle:pressed {{
    background:#181a20;
    color:#c7cbd2;
}}
QPushButton#reasoningToggle[expanded="true"] {{
    background:transparent;
    color:#9299a4;
}}
QLabel#reasoningBody {{
    background:transparent;
    border:none;
    border-left:1px solid #30333a;
    margin-left:10px;
    padding:4px 8px 5px 10px;
    color:#8f96a1;
    font-family:{theme.FONT_UI};
    font-size:11px;
}}
"""


_ACTION_LOCAL_QSS = """
QWidget#messageActions {
    background:transparent;
}
QPushButton#messageAction {
    background:transparent;
    border:none;
    border-radius:6px;
    padding:0;
}
QPushButton#messageAction:hover {
    background:#17191e;
}
QPushButton#messageAction:pressed,
QPushButton#messageAction:checked {
    background:#1b1d23;
}
QLabel#messageActionTime {
    background:transparent;
    color:#626a75;
    font-size:10px;
    padding:0;
}
"""


_GLOBAL_READING_QSS = f"""
/* Assistant prose is the primary reading surface, not a card. */
QFrame#assistantMessage,
QFrame#assistantMessage:hover {{
    background:transparent;
    border:none;
    border-radius:0;
}}
QFrame#assistantMessage QLabel#messageBody {{
    background:transparent;
    color:#dde0e6;
    font-size:14px;
}}

/* Fenced code keeps containment, but the border no longer looks embossed. */
QFrame#codeBlock {{
    background:#15171c;
    border:1px solid #24272e;
    border-radius:7px;
}}
QLabel#codeLanguage {{
    color:#737b86;
    font-family:{theme.FONT_MONO};
    font-size:10px;
    font-weight:550;
}}
QPlainTextEdit#codeBody {{
    background:transparent;
    border:none;
    color:#c1c6ce;
    font-family:{theme.FONT_MONO};
    font-size:11px;
    padding:0;
    selection-background-color:#343842;
}}
QPushButton#copyButton {{
    background:transparent;
    border:none;
    border-radius:5px;
    color:#747c87;
    font-size:10px;
    font-weight:540;
}}
QPushButton#copyButton:hover {{
    background:#1d1f25;
    border:none;
    color:#b8bec7;
}}
"""


def _install_activity_density() -> None:
    original_init = output_presentation.FlatActivityCard.__init__
    original_update = output_presentation.FlatActivityCard.update_card

    def activity_init(self: Any, kind: str, parent: Any = None) -> None:
        original_init(self, kind, parent)
        self.setStyleSheet(self.styleSheet() + "\n" + _ACTIVITY_LOCAL_QSS)
        self.icon.setFixedSize(17, 17)
        self.icon.framed = False

        outer = self.layout()
        if outer is not None:
            outer.setContentsMargins(0, 2, 0, 3)
            outer.setSpacing(4)
            if outer.count():
                header = outer.itemAt(0).layout()
                if header is not None:
                    header.setSpacing(7)

        shell = self.body_shell.layout()
        if shell is not None:
            shell.setContentsMargins(11, 8, 11, 8)
            shell.setSpacing(5)

    def activity_update(
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
        # Completion is the common case and should visually disappear into the
        # transcript. Running state already pulses the icon; only exceptional
        # states deserve persistent status text.
        if status == "completed":
            self.icon.set_tone("muted")
        self.status_label.setVisible(
            bool(self.status_label.text())
            and status in {"failed", "denied", "cancelled", "waiting", "waiting_approval"}
        )

    output_presentation.FlatActivityCard.__init__ = activity_init
    output_presentation.FlatActivityCard.update_card = activity_update


def _install_message_density() -> None:
    original_init = output_presentation.MessageWidget.__init__

    def message_init(self: Any, role: str, parent: Any = None) -> None:
        original_init(self, role, parent)
        if role != "assistant":
            return
        layout = self.layout()
        if layout is not None:
            layout.setContentsMargins(0, 1, 0, 2)
            layout.setSpacing(3)
        body_layout = getattr(self, "body_layout", None)
        if body_layout is not None:
            body_layout.setSpacing(5)

    output_presentation.MessageWidget.__init__ = message_init


def _install_reasoning_density() -> None:
    # Import here so transcript_disclosure has already built its canonical
    # classes. Patching the constructor still affects every future message.
    from app.desktop import transcript_disclosure

    original_init = transcript_disclosure.ReasoningDisclosure.__init__

    def reasoning_init(self: Any, parent: Any = None) -> None:
        original_init(self, parent)
        self.setStyleSheet(self.styleSheet() + "\n" + _REASONING_LOCAL_QSS)
        layout = self.layout()
        if layout is not None:
            layout.setSpacing(2)

    transcript_disclosure.ReasoningDisclosure.__init__ = reasoning_init


def _install_action_density() -> None:
    original_init = message_actions.MessageActionBar.__init__

    def action_init(self: Any, message: Any) -> None:
        original_init(self, message)
        self.setStyleSheet(self.styleSheet() + "\n" + _ACTION_LOCAL_QSS)
        self.setFixedHeight(26)
        row = self.layout()
        if row is not None:
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(0)
        for button in (self.copy_button, self.dislike_button, self.open_button):
            button.setFixedSize(24, 24)
        self.time_label.setFixedHeight(24)
        self.time_label.setMinimumWidth(38)
        self.time_label.setContentsMargins(6, 0, 0, 0)

    message_actions.MessageActionBar.__init__ = action_init


def install() -> None:
    """Install the final conversation reading hierarchy."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # RichLabel reads this dynamically for assistant blocks. The outgoing user
    # bubble already cached its own compact CSS earlier in the import pipeline.
    theme.MESSAGE_CSS = _ASSISTANT_DOCUMENT_CSS

    original_stylesheet = theme.stylesheet

    def stylesheet() -> str:
        return original_stylesheet() + _GLOBAL_READING_QSS

    theme.stylesheet = stylesheet

    _install_activity_density()
    _install_message_density()
    _install_reasoning_density()
    _install_action_density()


__all__ = ["install"]
