from __future__ import annotations

import pytest

pytest.importorskip("browser_use")

from browser_use.actor.element import Element
from browser_use.actor.page import Page


def test_browser_use_actor_exposes_loom_interaction_primitives():
    assert callable(Element.hover)
    assert callable(Element.select_option)
    assert callable(Element.drag_to)
    assert callable(Page.press)
