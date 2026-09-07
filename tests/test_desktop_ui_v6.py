from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from app.desktop_ui import LoomDesktopWindow
from app.desktop_ui_v6 import LoomDesktopWindow as LoomDesktopWindowV6, _rich_blocks


def test_active_desktop_entrypoint_preserves_v6_visual_layer():
    assert issubclass(LoomDesktopWindow, LoomDesktopWindowV6)
    assert LoomDesktopWindow.__module__ == "app.desktop_ui_v9"


def test_v6_transcript_markup_is_readable_and_html_safe():
    rendered = _rich_blocks(
        "# Result\n\n"
        "**Ready** with `inline_code`.\n\n"
        "- [x] inspected\n"
        "- [ ] follow up\n\n"
        "> concise note\n\n"
        "```python\n"
        "print('<unsafe>')\n"
        "```"
    )

    assert "<div class='h1'>Result</div>" in rendered
    assert "<strong>Ready</strong>" in rendered
    assert "<code>inline_code</code>" in rendered
    assert "class='check done'" in rendered
    assert "class='check todo'" in rendered
    assert "<blockquote>concise note</blockquote>" in rendered
    assert "print('&lt;unsafe&gt;')" in rendered
    assert "<unsafe>" not in rendered
