"""Refine Loom's generic tool glyph.

The old wrench compressed into a tiny key/link shape once it was placed inside
VectorIcon's framed optical box.  This keeps the existing icon system and tone
routing, but gives the generic tool mark a larger, cleaner open-jaw wrench that
reads correctly at 18–40 px on Windows HiDPI displays.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter, QPainterPath

from app.desktop import iconography


_INSTALLED = False


def _tool_box(box: QRectF) -> QRectF:
    """Use a slightly larger optical box without touching every other glyph."""
    grow = min(box.width(), box.height()) * 0.075
    return box.adjusted(-grow, -grow, grow, grow)


def _draw_refined_tool(painter: QPainter, box: QRectF) -> None:
    """Product-style open-jaw wrench with a quiet ring handle.

    The silhouette intentionally uses only two primitives: one continuous jaw
    stroke and one handle/ring.  At small sizes this is much easier to recognise
    than the previous closed mini-path, which visually collapsed into two dots
    joined by a diagonal line.
    """
    box = _tool_box(box)
    p = iconography._p

    # Open jaw.  The two short inner faces create an unmistakable wrench mouth,
    # while the outer cubic keeps the silhouette soft and consistent with the
    # rest of Loom's rounded line-icon language.
    jaw = QPainterPath()
    jaw.moveTo(p(box, 14.0, 9.5))
    jaw.cubicTo(p(box, 12.9, 7.0), p(box, 13.8, 4.0), p(box, 16.2, 2.9))
    jaw.lineTo(p(box, 16.0, 6.15))
    jaw.lineTo(p(box, 18.15, 8.25))
    jaw.lineTo(p(box, 21.05, 6.55))
    jaw.cubicTo(p(box, 20.25, 9.25), p(box, 17.05, 10.65), p(box, 14.0, 9.5))
    painter.drawPath(jaw)

    # Straight handle with a slightly larger ring.  Keeping the shaft separate
    # from the jaw prevents antialiasing from turning the whole glyph into a
    # single diagonal stroke at 100–125% Windows scaling.
    iconography._line(painter, box, 14.25, 9.35, 8.15, 15.45)
    iconography._ellipse(painter, box, 3.25, 15.15, 5.7, 5.7)


def install() -> None:
    """Install the refined drawer without changing tool routing or state tones."""
    global _INSTALLED
    if _INSTALLED:
        return
    iconography._DRAWERS["tool"] = _draw_refined_tool
    _INSTALLED = True


__all__ = ["install"]
