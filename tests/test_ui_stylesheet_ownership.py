import re
from pathlib import Path

import pytest

from ui_stylesheet_ownership import owns_component_layout


def test_shared_motion_is_allowed_only_for_glyph_properties():
    selector = re.compile(r"\.codex-sidebar")
    assert not owns_component_layout(Path("icon-motion.css"), selector,
        ".codex-sidebar svg { rotate: 3deg; transform-origin: center; }")
    assert owns_component_layout(Path("sidebar.css"), selector,
        ".codex-sidebar { width: 240px; }")


@pytest.mark.parametrize("css", [
    ".codex-sidebar svg { width: 30px; }",
    ".codex-sidebar { transform: translateY(2px); }",
    ".codex-sidebar { overflow: hidden; }",
])
def test_shared_motion_cannot_override_component_geometry(css):
    with pytest.raises(AssertionError):
        owns_component_layout(Path("icon-motion.css"), re.compile(r"\.codex-sidebar"), css)
