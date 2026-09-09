"""Composited side-panel transitions for Loom's native desktop UI.

The left library, conversation transcript and Runtime inspector are all sizeable
QWidget trees. Animating ``QSplitter.setSizes`` on every frame makes Qt resize
and repaint those trees on the GUI thread, which stutters badly once a real
thread contains many messages and Runtime rows.

This module keeps live layout and visual motion separate:

* capture the settled splitter panes once;
* commit the requested real splitter layout once under an overlay;
* capture the settled destination once;
* freeze live splitter painting for the ~200 ms transition;
* animate only a lightweight snapshot overlay;
* reveal the already-laid-out destination and repaint it once at the endpoint.

The overlay interpolates the three pane boundaries with one progress value. The
conversation therefore retreats as either side panel opens, while panel content
is revealed by clipping rather than by repeatedly resizing hundreds of widgets.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEasingCurve, QPoint, QRect, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSplitter, QWidget

from app.desktop import sidebar_motion, theme


_OPEN_DURATION_MS = 210
_CLOSE_DURATION_MS = 190
_MIN_REVERSAL_MS = 80
_CENTER_CROSSFADE_START = 0.62


def _restore_constraints(controller: Any, key: str, panel: QWidget) -> None:
    minimum, maximum = controller._panel_constraints[key]
    panel.setMinimumWidth(minimum)
    panel.setMaximumWidth(maximum)
    panel.updateGeometry()


def _center_index(splitter: QSplitter, panel_index: int) -> int:
    count = splitter.count()
    if count <= 1:
        return -1
    if panel_index == 0:
        return 1
    if panel_index == count - 1:
        return count - 2
    return max(0, panel_index - 1)


def _transition_duration(
    *,
    opening: bool,
    start_progress: float = 0.0,
    end_progress: float = 1.0,
) -> int:
    """Scale duration to remaining visual distance, useful for reversals."""
    base = _OPEN_DURATION_MS if opening else _CLOSE_DURATION_MS
    fraction = min(1.0, max(0.0, abs(float(end_progress) - float(start_progress))))
    scaled = int(round(base * max(0.42, fraction ** 0.6)))
    return max(_MIN_REVERSAL_MS, min(base, scaled))


def _lerp_rect(start: QRect, end: QRect, progress: float) -> QRect:
    p = max(0.0, min(1.0, float(progress)))
    return QRect(
        int(round(start.x() + (end.x() - start.x()) * p)),
        int(round(start.y() + (end.y() - start.y()) * p)),
        max(0, int(round(start.width() + (end.width() - start.width()) * p))),
        max(0, int(round(start.height() + (end.height() - start.height()) * p))),
    )


def _pane_rects(splitter: QSplitter) -> list[QRect]:
    """Return paint-space pane rects, normalising hidden side panes to zero."""
    count = splitter.count()
    height = max(0, splitter.height())
    width = max(0, splitter.width())
    result: list[QRect] = []
    for index in range(count):
        widget = splitter.widget(index)
        if widget is None:
            result.append(QRect())
            continue
        rect = QRect(widget.geometry())
        if widget.isVisible():
            result.append(rect)
            continue
        if index == 0:
            result.append(QRect(0, rect.y(), 0, height))
        elif index == count - 1:
            result.append(QRect(width, rect.y(), 0, height))
        else:
            result.append(QRect(rect.x(), rect.y(), 0, height))
    return result


def _capture_widget(widget: QWidget | None) -> QPixmap | None:
    if widget is None or not widget.isVisible() or widget.width() <= 0 or widget.height() <= 0:
        return None
    # QWidget.grab() performs one explicit render and preserves devicePixelRatio,
    # so the overlay stays crisp on Windows fractional-DPI displays.
    return widget.grab()


def _pane_snapshots(splitter: QSplitter) -> list[QPixmap | None]:
    return [_capture_widget(splitter.widget(index)) for index in range(splitter.count())]


def _capture_splitter(splitter: QSplitter) -> QPixmap:
    return splitter.grab()


def _logical_width(pixmap: QPixmap) -> float:
    ratio = float(pixmap.devicePixelRatio())
    return float(pixmap.width()) / ratio if ratio > 0 else float(pixmap.width())


class _SnapshotOverlay(QWidget):
    """Cheap compositor that animates three static pane snapshots."""

    def __init__(
        self,
        splitter: QSplitter,
        *,
        before_rects: list[QRect],
        before_panes: list[QPixmap | None],
        before_full: QPixmap,
    ) -> None:
        host = splitter.parentWidget() or splitter
        super().__init__(host)
        self.splitter = splitter
        self.before_rects = [QRect(rect) for rect in before_rects]
        self.after_rects = [QRect(rect) for rect in before_rects]
        self.before_panes = list(before_panes)
        self.after_panes = list(before_panes)
        self.before_full = before_full
        self.after_full = before_full
        self.progress = 0.0

        self.setObjectName("sidebarSnapshotTransition")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.sync_geometry()
        self.show()
        self.raise_()

    def sync_geometry(self) -> None:
        host = self.parentWidget()
        if host is None:
            return
        top_left = self.splitter.mapTo(host, QPoint(0, 0))
        self.setGeometry(QRect(top_left, self.splitter.size()))
        self.raise_()

    def set_destination(
        self,
        *,
        after_rects: list[QRect],
        after_panes: list[QPixmap | None],
        after_full: QPixmap,
    ) -> None:
        self.after_rects = [QRect(rect) for rect in after_rects]
        self.after_panes = list(after_panes)
        self.after_full = after_full
        self.update()

    def set_progress(self, progress: float) -> None:
        self.progress = max(0.0, min(1.0, float(progress)))
        self.update()

    def _draw_pixmap_anchored(
        self,
        painter: QPainter,
        pixmap: QPixmap | None,
        rect: QRect,
        *,
        anchor_right: bool = False,
        opacity: float = 1.0,
    ) -> None:
        if pixmap is None or rect.width() <= 0 or rect.height() <= 0 or opacity <= 0:
            return
        painter.save()
        painter.setClipRect(rect)
        painter.setOpacity(max(0.0, min(1.0, float(opacity))))
        if anchor_right:
            draw_x = int(round(rect.right() + 1 - _logical_width(pixmap)))
        else:
            draw_x = rect.x()
        painter.drawPixmap(draw_x, rect.y(), pixmap)
        painter.restore()

    def paintEvent(self, _event: Any) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.BG_APP))

        # Exact endpoint snapshots eliminate any one-frame mismatch in handles,
        # fonts, scrollbars or fractional-DPI rounding.
        if self.progress <= 0.0001:
            painter.drawPixmap(0, 0, self.before_full)
            return
        if self.progress >= 0.9999:
            painter.drawPixmap(0, 0, self.after_full)
            return

        count = min(len(self.before_rects), len(self.after_rects))
        if count <= 0:
            return
        rects = [
            _lerp_rect(self.before_rects[index], self.after_rects[index], self.progress)
            for index in range(count)
        ]

        # The conversation is the only pane whose content width changes
        # materially. Draw destination underneath, then keep the origin snapshot
        # opaque for most of the trip. That makes its content move as one surface
        # and only re-wrap visually near the settled endpoint.
        center = 1 if count >= 3 else max(0, count // 2)
        center_rect = rects[center]
        after_center = self.after_panes[center] if center < len(self.after_panes) else None
        before_center = self.before_panes[center] if center < len(self.before_panes) else None
        self._draw_pixmap_anchored(painter, after_center, center_rect)

        if self.progress <= _CENTER_CROSSFADE_START:
            old_opacity = 1.0
        else:
            tail = (self.progress - _CENTER_CROSSFADE_START) / (1.0 - _CENTER_CROSSFADE_START)
            old_opacity = max(0.0, 1.0 - tail)
        self._draw_pixmap_anchored(
            painter,
            before_center,
            center_rect,
            opacity=old_opacity,
        )

        # Side panes are drawers: keep their content pinned to the outside edge
        # and reveal/hide it only through the interpolated clip rectangle.
        for index in range(count):
            if index == center:
                continue
            before_rect = self.before_rects[index]
            after_rect = self.after_rects[index]
            opening = after_rect.width() > before_rect.width()
            pixmap = (
                self.after_panes[index]
                if opening and index < len(self.after_panes)
                else self.before_panes[index] if index < len(self.before_panes) else None
            )
            self._draw_pixmap_anchored(
                painter,
                pixmap,
                rects[index],
                anchor_right=index == count - 1,
            )

        # One restrained divider keeps the moving edges visually locked together
        # even though QSplitter itself is no longer repainting each frame.
        painter.setOpacity(1.0)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        if count >= 2 and rects[0].width() > 0:
            painter.drawLine(rects[0].right(), 0, rects[0].right(), self.height())
        if count >= 3 and rects[-1].width() > 0:
            painter.drawLine(rects[-1].left(), 0, rects[-1].left(), self.height())


def _commit_panel_state(
    controller: Any,
    key: str,
    panel: QWidget,
    *,
    visible: bool,
    width: int,
) -> None:
    """Commit one real splitter state exactly once; no animation lives here."""
    splitter = getattr(controller.window, "main_splitter", None)
    if not isinstance(splitter, QSplitter):
        panel.setVisible(bool(visible))
        _restore_constraints(controller, key, panel)
        return

    index = splitter.indexOf(panel)
    center = _center_index(splitter, index)
    if index < 0 or center < 0:
        panel.setVisible(bool(visible))
        _restore_constraints(controller, key, panel)
        return

    _minimum, maximum = controller._panel_constraints[key]
    panel.setGraphicsEffect(None)
    panel.setMinimumWidth(0)
    panel.setMaximumWidth(maximum)
    splitter.setCollapsible(index, True)

    if visible and not panel.isVisible():
        panel.show()
        try:
            splitter.refresh()
        except AttributeError:
            pass

    sizes = list(splitter.sizes())
    if index >= len(sizes) or center >= len(sizes):
        _restore_constraints(controller, key, panel)
        splitter.setCollapsible(index, False)
        panel.setVisible(bool(visible))
        return

    current_width = max(0, int(sizes[index])) if panel.isVisible() else 0
    desired_width = max(0, int(width)) if visible else 0
    delta = desired_width - current_width
    sizes[index] = desired_width
    sizes[center] = max(0, int(sizes[center]) - delta)
    splitter.setSizes(sizes)

    if not visible:
        panel.hide()

    _restore_constraints(controller, key, panel)
    splitter.setCollapsible(index, False)
    try:
        splitter.refresh()
    except AttributeError:
        pass


class _PanelSnapshotTransition:
    """One shared visual transition; the live splitter already sits at an endpoint."""

    def __init__(
        self,
        controller: Any,
        key: str,
        panel: QWidget,
        target_visible: bool,
    ) -> None:
        self.controller = controller
        self.key = key
        self.panel = panel
        self.splitter: QSplitter = controller.window.main_splitter
        self.before_visible = bool(panel.isVisible())
        self.after_visible = bool(target_visible)
        self.before_width = max(0, int(panel.width())) if self.before_visible else 0

        original_min, original_max = controller._panel_constraints[key]
        if not self.after_visible and self.before_width > 0:
            controller._panel_widths[key] = min(self.before_width, original_max)
        self.after_width = (
            min(max(original_min, int(controller._panel_widths[key])), original_max)
            if self.after_visible
            else 0
        )

        self.before_rects = _pane_rects(self.splitter)
        self.before_panes = _pane_snapshots(self.splitter)
        self.before_full = _capture_splitter(self.splitter)

        self.overlay = _SnapshotOverlay(
            self.splitter,
            before_rects=self.before_rects,
            before_panes=self.before_panes,
            before_full=self.before_full,
        )
        # Paint the exact old frame before the real splitter is touched.
        self.overlay.repaint()

        _commit_panel_state(
            controller,
            key,
            panel,
            visible=self.after_visible,
            width=self.after_width,
        )
        self.after_rects = _pane_rects(self.splitter)
        self.after_panes = _pane_snapshots(self.splitter)
        self.after_full = _capture_splitter(self.splitter)
        self.overlay.set_destination(
            after_rects=self.after_rects,
            after_panes=self.after_panes,
            after_full=self.after_full,
        )

        # Everything under the overlay is already in the requested final state.
        # Stop live painting until the compositor settles so Agent/Runtime updates
        # cannot steal animation frames on the GUI thread.
        self.splitter.setUpdatesEnabled(False)

        self.animation: QVariantAnimation | None = None
        self.destination = 1.0
        self._finished = False
        self.animate_to(1.0)

    def animate_to(self, destination: float) -> None:
        destination = 1.0 if float(destination) >= 0.5 else 0.0
        current = float(self.overlay.progress)
        self.destination = destination
        if self.animation is not None:
            self.animation.stop()
            self.animation.deleteLater()
            self.animation = None
        if abs(current - destination) <= 0.0001:
            self._finish()
            return

        opening = self.after_visible if destination >= 0.5 else self.before_visible
        animation = QVariantAnimation(self.controller)
        animation.setStartValue(current)
        animation.setEndValue(destination)
        animation.setDuration(
            _transition_duration(
                opening=bool(opening),
                start_progress=current,
                end_progress=destination,
            )
        )
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(lambda value: self.overlay.set_progress(float(value)))
        animation.finished.connect(self._finish)
        self.animation = animation
        self.controller._panel_animations[self.key] = animation
        animation.start()

    def request_visible(self, visible: bool) -> bool:
        if bool(visible) == self.after_visible:
            self.animate_to(1.0)
            return True
        if bool(visible) == self.before_visible:
            self.animate_to(0.0)
            return True
        return False

    def _finish(self) -> None:
        if self._finished:
            return
        endpoint_after = self.destination >= 0.5
        final_visible = self.after_visible if endpoint_after else self.before_visible
        final_width = self.after_width if endpoint_after else self.before_width

        if not endpoint_after:
            _commit_panel_state(
                self.controller,
                self.key,
                self.panel,
                visible=final_visible,
                width=final_width,
            )

        if final_visible and final_width > 0:
            self.controller._panel_widths[self.key] = final_width

        self.overlay.set_progress(1.0 if endpoint_after else 0.0)
        self.overlay.repaint()

        # Repaint the live destination once while the overlay still covers it,
        # then remove the compositor. This avoids an endpoint flash.
        self.splitter.setUpdatesEnabled(True)
        self.splitter.repaint()
        self.overlay.hide()
        self.overlay.deleteLater()

        if self.controller._panel_animations.get(self.key) is self.animation:
            self.controller._panel_animations.pop(self.key, None)
        if getattr(self.controller, "_panel_snapshot_transition", None) is self:
            self.controller._panel_snapshot_transition = None

        if self.animation is not None:
            self.animation.deleteLater()
            self.animation = None
        self._finished = True

        pending = getattr(self.controller, "_panel_snapshot_pending", None)
        if pending is not None:
            self.controller._panel_snapshot_pending = None
            pending_key, pending_panel, pending_visible = pending
            self.controller.set_panel_visible(
                pending_key,
                pending_panel,
                pending_visible,
            )


def _set_panel_visible(
    self: Any,
    key: str,
    panel: QWidget,
    visible: bool,
) -> None:
    """Animate side-panel chrome without live-resizing its QWidget tree."""
    splitter = getattr(self.window, "main_splitter", None)
    if not isinstance(splitter, QSplitter):
        panel.setVisible(bool(visible))
        _restore_constraints(self, key, panel)
        return

    if not theme.motion_enabled():
        active = getattr(self, "_panel_snapshot_transition", None)
        if active is not None:
            active.overlay.hide()
            active.splitter.setUpdatesEnabled(True)
            self._panel_snapshot_transition = None
        original_min, original_max = self._panel_constraints[key]
        width = min(max(original_min, int(self._panel_widths[key])), original_max)
        _commit_panel_state(self, key, panel, visible=bool(visible), width=width)
        return

    active = getattr(self, "_panel_snapshot_transition", None)
    if active is not None and not active._finished:
        if active.key == key and active.request_visible(bool(visible)):
            return
        # One compositor owns the whole splitter so both edges and the center use
        # one clock. If the opposite side is clicked during the ~200 ms motion,
        # keep only the latest request and start it the instant this pass settles.
        self._panel_snapshot_pending = (key, panel, bool(visible))
        return

    if bool(panel.isVisible()) == bool(visible):
        _restore_constraints(self, key, panel)
        return

    transition = _PanelSnapshotTransition(self, key, panel, bool(visible))
    self._panel_snapshot_transition = transition


def install() -> None:
    """Replace live QSplitter resize animation with snapshot compositing."""
    sidebar_motion.SidebarMotionController.set_panel_visible = _set_panel_visible


__all__ = [
    "_PanelSnapshotTransition",
    "_SnapshotOverlay",
    "_commit_panel_state",
    "_lerp_rect",
    "_pane_rects",
    "_transition_duration",
    "install",
]
