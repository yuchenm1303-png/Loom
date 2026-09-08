from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.agent_runtime.contracts import PermissionMode
from app.desktop.composer import (
    MAX_HEIGHT,
    MIN_HEIGHT,
    PERMISSION_DETAIL,
    PERMISSION_MODES,
    AddModelDialog,
    ComposerPanel,
    ControlButton,
)


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def composer(qt_app):
    panel = ComposerPanel()
    panel.resize(880, 120)
    # The editor only measures its document once it has been laid out.
    panel.show()
    qt_app.processEvents()
    yield panel
    panel.close()


def test_every_runtime_permission_mode_is_offered_and_described():
    # The menu must not drift from the modes the runtime actually enforces.
    assert PERMISSION_MODES == tuple(mode.value for mode in PermissionMode)
    assert set(PERMISSION_DETAIL) == set(PERMISSION_MODES)
    assert all(PERMISSION_DETAIL[mode].strip() for mode in PERMISSION_MODES)


def test_the_panel_adopts_the_modes_the_server_reports(composer):
    composer.set_permission_modes(["read-only", "workspace"])
    assert composer._permission_modes == ("read-only", "workspace")

    # An server that reports nothing usable falls back rather than emptying the menu.
    composer.set_permission_modes([])
    assert composer._permission_modes == PERMISSION_MODES


def test_an_open_conversation_shows_its_own_permission_mode(composer):
    composer.set_permission("full-access", thread_mode="read-only")

    # A Thread keeps the mode it was created with, so that is what governs the
    # next message even after the default has been changed.
    assert composer.permission_button.value == "read-only"
    assert "fixed at read-only" in composer.permission_button.toolTip()
    assert "new conversations will use full-access" in composer.permission_button.toolTip().casefold()


def test_a_draft_shows_the_pending_default(composer):
    composer.set_permission("workspace", thread_mode="")
    assert composer.permission_button.value == "workspace"
    assert PERMISSION_DETAIL["workspace"] in composer.permission_button.toolTip()


def test_the_model_control_is_disabled_when_it_cannot_do_anything(composer):
    composer.set_model("qwen-plus", locked_reason="Model is fixed for this window.")
    assert composer.model_button.value == "qwen-plus"
    assert composer.model_button.isEnabled() is False

    composer.set_model("qwen-plus", history=["qwen-max", "qwen-plus"])
    assert composer.model_button.isEnabled() is True
    # The current model is not repeated in its own history.
    assert composer._model_history == ["qwen-max"]


def test_model_api_dialog_changes_base_url_policy_with_adapter(qt_app):
    dialog = AddModelDialog()
    try:
        assert dialog.adapter() == "openai-compatible"
        assert dialog.base_url_edit.isEnabled() is True

        dialog.adapter_combo.setCurrentIndex(1)
        qt_app.processEvents()
        assert dialog.adapter() == "openai"
        assert dialog.base_url_edit.isEnabled() is False
        assert dialog.base_url_edit.text() == ""

        dialog.adapter_combo.setCurrentIndex(0)
        qt_app.processEvents()
        assert dialog.base_url_edit.isEnabled() is True
    finally:
        dialog.close()


def test_there_is_no_effort_control(composer):
    # The runtime registers one model role with one binding, so an effort
    # selector would have nothing to select between.
    controls = [
        button.value for button in composer.findChildren(ControlButton)
    ]
    assert len(controls) == 3  # workspace, permission, model
    assert not hasattr(composer, "effort_button")


def test_the_editor_grows_with_its_content_and_stops(composer, qt_app):
    assert composer.editor.height() == MIN_HEIGHT

    composer.set_text("line\n" * 60)
    qt_app.processEvents()
    assert composer.editor.height() == MAX_HEIGHT

    # It shrinks back rather than staying tall once the text is short again.
    composer.set_text("one line")
    qt_app.processEvents()
    assert composer.editor.height() == MIN_HEIGHT


def test_submitting_emits_the_trimmed_text_once(composer):
    sent = []
    composer.submitted.connect(sent.append)

    composer.set_text("   ")
    composer.editor.sendRequested.emit()
    assert sent == []

    composer.set_text("  do the thing  ")
    composer.editor.sendRequested.emit()
    assert sent == ["do the thing"]


def test_a_disabled_send_button_blocks_submission(composer):
    sent = []
    composer.submitted.connect(sent.append)
    composer.set_text("hello")
    composer.set_busy(active=True, can_send=False, read_only=False)

    composer.editor.sendRequested.emit()
    assert sent == []
    assert composer.stop_button.isHidden() is False


def test_busy_state_drives_stop_send_and_read_only(composer):
    composer.set_busy(active=False, can_send=True, read_only=False)
    assert composer.stop_button.isHidden() is True
    assert composer.send_button.isEnabled() is True
    assert composer.editor.isReadOnly() is False

    composer.set_busy(active=False, can_send=False, read_only=True)
    assert composer.editor.isReadOnly() is True
    assert composer.send_button.isEnabled() is False
