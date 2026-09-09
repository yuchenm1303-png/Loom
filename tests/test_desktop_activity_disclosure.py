from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import app.desktop  # noqa: F401 - installs desktop presentation hooks
from app.desktop.activity_disclosure import DisclosureChevron, _header_layout
from app.desktop.output_presentation import FlatActivityCard


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _card(app) -> FlatActivityCard:
    card = FlatActivityCard("diff")
    card.resize(720, 120)
    card.update_card(
        title="Edited disk_scan.ps1",
        status="completed",
        body="Changes\n+ one\n+ two",
        auto_expand=False,
    )
    card.show()
    app.processEvents()
    return card


def test_activity_uses_refined_native_chevron(app):
    card = _card(app)
    assert isinstance(card.toggle_button, DisclosureChevron)
    assert card.toggle_button.text() == ""
    assert card.toggle_button.size().width() == 20
    card.toggle_button.set_expanded(True, animate=False)
    assert card.toggle_button.progress == pytest.approx(1.0)
    card.toggle_button.set_expanded(False, animate=False)
    assert card.toggle_button.progress == pytest.approx(0.0)
    card.close()


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        ("tool", "failed"),
        # A live process is the deliberate exception: ``runtime_feedback``
        # always opens it so the reader sees the in-flight output, even when
        # ``auto_expand=True`` came from the source.  Everything else still
        # collapses on default and only opens when the user asks.
        ("process", "failed"),
        ("diff", "completed"),
        ("error", "failed"),
    ],
)
def test_activity_details_default_to_collapsed_even_when_source_requests_expansion(
    app, kind, status
):
    card = FlatActivityCard(kind)
    card.resize(720, 120)
    card.update_card(
        title="Example activity",
        status=status,
        body="details",
        auto_expand=True,
    )
    card.show()
    app.processEvents()

    assert card._expanded is False
    assert card.body_shell.isHidden()
    assert card.toggle_button.progress == pytest.approx(0.0)
    card.close()


def test_clicking_activity_header_toggles_details(app):
    card = _card(app)
    assert card._expanded is False
    header = _header_layout(card)
    assert header is not None

    QTest.mouseClick(
        card,
        Qt.MouseButton.LeftButton,
        pos=header.geometry().center(),
    )
    app.processEvents()
    assert card._expanded is True

    QTest.mouseClick(
        card,
        Qt.MouseButton.LeftButton,
        pos=header.geometry().center(),
    )
    app.processEvents()
    assert card._expanded is False
    card.close()


def test_user_opened_activity_stays_open_across_status_updates(app):
    card = _card(app)
    card._toggle()
    app.processEvents()
    assert card._expanded is True
    assert card._user_toggled is True

    card.update_card(
        title="Edited disk_scan.ps1",
        status="completed",
        body="Changes\n+ one\n+ two\n+ three",
        auto_expand=False,
    )
    app.processEvents()

    assert card._expanded is True
    assert not card.body_shell.isHidden()
    card.close()


def test_clicking_expanded_output_does_not_toggle_card(app):
    card = _card(app)
    card._expanded = True
    card._sync_body(animate=False)
    app.processEvents()
    assert card._expanded is True

    QTest.mouseClick(
        card.body.viewport(),
        Qt.MouseButton.LeftButton,
        pos=card.body.viewport().rect().center(),
    )
    app.processEvents()
    assert card._expanded is True
    card.close()
