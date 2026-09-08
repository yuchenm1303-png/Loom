"""Ultra-compact density pass for the main transcript's Runtime activity rows.

The central conversation should read like a continuous execution log when many
tool/process events arrive back-to-back. Runtime inspector tabs deliberately
keep their richer spacing; this hook only affects the lightweight activity rows
used in the main transcript.
"""

from __future__ import annotations

from typing import Any

from app.desktop import output_presentation as presentation


_INSTALLED = False
_MAIN_TRANSCRIPT_SPACING = 0


def install() -> None:
    """Install the dense execution-log presentation once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_view_init = presentation.TranscriptView.__init__
    original_card_init = presentation.FlatActivityCard.__init__
    original_card_update = presentation.FlatActivityCard.update_card

    def compact_view_init(self: Any, *args: Any, **kwargs: Any) -> None:
        # Runtime CardListView supplies an explicit spacing value. Only the main
        # transcript inherits our zero-gap execution-log density.
        kwargs.setdefault("spacing", _MAIN_TRANSCRIPT_SPACING)
        original_view_init(self, *args, **kwargs)

    def compact_card_init(self: Any, kind: str, parent: Any = None) -> None:
        original_card_init(self, kind, parent)

        outer = self.layout()
        if outer is not None:
            # Collapsed activity rows should reserve only the height of one text
            # line. Expanded output keeps its own internal padding below.
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(1)

            header = outer.itemAt(0).layout() if outer.count() else None
            if header is not None:
                header.setContentsMargins(0, 0, 0, 0)
                header.setSpacing(5)

                for index in range(header.count()):
                    nested = header.itemAt(index).layout()
                    if nested is not None:
                        nested.setContentsMargins(0, 0, 0, 0)
                        nested.setSpacing(0)

        # Match the compact rows in mature agent UIs: tiny semantic glyph,
        # one-line action summary, and a small disclosure affordance.
        self.icon.setFixedSize(16, 16)
        self.toggle_button.setFixedSize(18, 18)
        self.title_label.setContentsMargins(0, 0, 0, 0)
        self.subtitle_label.setContentsMargins(0, 0, 0, 0)
        self.title_label.setStyleSheet(
            "background:transparent; color:#eceff4; font-size:12px; "
            "font-weight:620; padding:0; margin:0;"
        )
        self.subtitle_label.setStyleSheet(
            "background:transparent; color:#969cad; font-size:10px; "
            "padding:0; margin:0;"
        )

        # Raw argv/cwd metadata is still available in the row tooltip and output;
        # keeping it visible under every tool name is what made the transcript
        # look like stacked mini-cards instead of a continuous execution log.
        self.subtitle_label.hide()
        self.updateGeometry()

    def compact_card_update(self: Any, *args: Any, **kwargs: Any) -> None:
        original_card_update(self, *args, **kwargs)
        # update_card may show the subtitle again when new Runtime data arrives.
        # Keep the central transcript strictly one-line while preserving the full
        # subtitle in the tooltip prepared by output_presentation.
        self.subtitle_label.hide()
        self.updateGeometry()

    presentation.TranscriptView.__init__ = compact_view_init
    presentation.FlatActivityCard.__init__ = compact_card_init
    presentation.FlatActivityCard.update_card = compact_card_update


__all__ = ["install"]
