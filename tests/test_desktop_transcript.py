from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.desktop.state import TranscriptEntry
from app.desktop.widgets import ActivityCard, MessageWidget, TranscriptView


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _message(key: str, text: str, *, kind: str = "assistant", streaming: bool = False):
    return TranscriptEntry(key=key, kind=kind, text=text, streaming=streaming)


def _settle(app, view, rounds: int = 40):
    for _ in range(rounds):
        app.processEvents()


def test_widgets_are_reused_across_renders(qt_app):
    view = TranscriptView()
    view.resize(700, 400)
    view.show()
    view.render([_message("a", "first"), _message("b", "second")])
    _settle(qt_app, view)

    identities = {key: id(view._widgets[key]) for key in view._order}
    view.render([_message("a", "first"), _message("b", "second changed")])
    _settle(qt_app, view)

    assert {key: id(view._widgets[key]) for key in view._order} == identities
    assert view._widgets["b"]._text == "second changed"
    view.close()


def test_removed_entries_are_dropped(qt_app):
    view = TranscriptView()
    view.resize(700, 400)
    view.render([_message("a", "one"), _message("b", "two")])
    _settle(qt_app, view)

    view.render([_message("b", "two")])
    _settle(qt_app, view)

    assert view._order == ["b"]
    assert "a" not in view._widgets
    view.close()


def test_canvas_height_tracks_wrapped_content_not_the_minimum_hint(qt_app):
    view = TranscriptView()
    view.resize(700, 420)
    view.show()
    view.render([_message("a", "word " * 400)])
    _settle(qt_app, view)

    width = view.viewport().width()
    # The scroll canvas must follow height-for-width; sizing it from the
    # wrapped label's minimumSizeHint leaves dead space below the last message.
    assert abs(view.canvas.height() - view.canvas.heightForWidth(width)) <= 40
    view.close()


def test_view_follows_new_content_and_stops_when_scrolled_up(qt_app):
    view = TranscriptView()
    view.resize(700, 320)
    view.show()
    view.render([_message(str(i), f"paragraph {i}\n\n" + "filler " * 60) for i in range(6)])
    _settle(qt_app, view)

    bar = view.verticalScrollBar()
    assert bar.maximum() > 0
    # Range settles asynchronously; following has to survive that.
    assert bar.value() == bar.maximum()

    bar.setValue(0)
    _settle(qt_app, view)
    assert view._follow_tail is False

    view.render(
        [_message(str(i), f"paragraph {i}\n\n" + "filler " * 60) for i in range(9)]
    )
    _settle(qt_app, view)
    assert bar.value() == 0

    view.scroll_to_tail()
    _settle(qt_app, view)
    assert bar.value() == bar.maximum()
    view.close()


def test_cards_are_built_for_non_message_entries(qt_app):
    view = TranscriptView()
    view.resize(700, 400)
    view.render(
        [
            _message("m", "hello"),
            TranscriptEntry(
                key="p",
                kind="process",
                item={"status": "completed", "argv": ["pytest", "-q"], "stdout": "ok\n"},
            ),
        ]
    )
    _settle(qt_app, view)

    assert isinstance(view._widgets["m"], MessageWidget)
    card = view._widgets["p"]
    assert isinstance(card, ActivityCard)
    assert card.title_label.text() == "$ pytest -q"
    assert card.status_label.text() == "Completed"
    view.close()


@pytest.mark.parametrize("reduced", [True, False])
def test_new_rows_fade_in_unless_reduced_motion_is_requested(qt_app, monkeypatch, reduced):
    if reduced:
        monkeypatch.setenv("LOOM_REDUCE_MOTION", "1")
    else:
        monkeypatch.delenv("LOOM_REDUCE_MOTION", raising=False)

    view = TranscriptView()
    view.resize(700, 400)
    view.render([_message("a", "first")])
    _settle(qt_app, view)
    # Rehydrating a thread must not animate; only later arrivals do.
    assert view._widgets["a"].graphicsEffect() is None

    view.render([_message("a", "first"), _message("b", "second")])
    effect = view._widgets["b"].graphicsEffect()
    assert (effect is None) is reduced

    _settle(qt_app, view, rounds=80)
    view.close()
