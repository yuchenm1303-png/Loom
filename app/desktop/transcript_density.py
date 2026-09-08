"""Compact density pass for the main transcript's Runtime activity rows.

The main conversation should read like a tight execution log when several tool
or process rows arrive back-to-back. Runtime inspector tabs already choose their
own spacing, so this hook only changes the default main-transcript density and
shrinks the lightweight command-row chrome a little.
"""

from __future__ import annotations

from typing import Any

from app.desktop import output_presentation as presentation


_INSTALLED = False
_MAIN_TRANSCRIPT_SPACING = 8


def install() -> None:
    """Install the density tweaks once without changing Runtime behavior."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_view_init = presentation.TranscriptView.__init__
    original_card_init = presentation.FlatActivityCard.__init__

    def compact_view_init(self: Any, *args: Any, **kwargs: Any) -> None:
        # Runtime CardListView passes its own spacing explicitly. Only the main
        # transcript previously inherited the roomy 20px default.
        kwargs.setdefault("spacing", _MAIN_TRANSCRIPT_SPACING)
        original_view_init(self, *args, **kwargs)

    def compact_card_init(self: Any, kind: str, parent: Any = None) -> None:
        original_card_init(self, kind, parent)

        outer = self.layout()
        if outer is not None:
            # These rows have no outer card chrome, so nine pixels of vertical
            # inset just reads as dead space between consecutive commands.
            outer.setContentsMargins(0, 1, 0, 1)
            outer.setSpacing(4)

            header = outer.itemAt(0).layout() if outer.count() else None
            if header is not None:
                header.setSpacing(6)
                # The title/subtitle stack is the only nested layout in the
                # header. Pull its two lines together without touching content.
                for index in range(header.count()):
                    nested = header.itemAt(index).layout()
                    if nested is not None:
                        nested.setSpacing(0)

        # Preserve the icon/chevron language, just reduce their footprint so a
        # one-line command row does not reserve a tall 26px band.
        self.icon.setFixedSize(18, 18)
        self.toggle_button.setFixedSize(24, 24)
        self.updateGeometry()

    presentation.TranscriptView.__init__ = compact_view_init
    presentation.FlatActivityCard.__init__ = compact_card_init


__all__ = ["install"]
