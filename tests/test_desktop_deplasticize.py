from __future__ import annotations


def test_flat_chrome_layer_is_installed_last() -> None:
    # Importing the desktop package installs the presentation stack in production
    # order.  The hierarchy pass must be present after component-specific polish
    # so gradients / bordered pills cannot silently become the final rule again.
    from app.desktop import theme

    css = theme.stylesheet()

    marker = "Shell: panels are regions, not cards."
    assert marker in css
    flat_start = css.rfind(marker)

    assert css.find("QFrame#composerFrame", flat_start) > flat_start
    assert css.find("background:#18191f", flat_start) > flat_start
    assert css.find("QPushButton#composerPermission[mode=\"full-access\"]", flat_start) > flat_start
    assert css.find("background:#7569df", flat_start) > flat_start
    assert css.find("QFrame#runtimeHeader", flat_start) > flat_start
    assert css.find("QLabel#statusChip", flat_start) > flat_start


def test_flat_chrome_keeps_only_the_model_as_a_persistent_secondary_surface() -> None:
    from app.desktop import theme

    css = theme.stylesheet()
    flat = css[css.rfind("Shell: panels are regions, not cards.") :]

    assert "QPushButton#composerAttach" in flat
    assert "QPushButton#composerWorkspace" in flat
    assert "background:transparent" in flat
    assert "QPushButton#composerModel" in flat
    assert "background:#292a2f" in flat
    assert "QLabel#composerUsage" in flat
    assert "border:none" in flat
