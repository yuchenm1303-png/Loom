"""Frame-coalesced rendering for live assistant and process output.

A Runtime stream may deliver dozens or hundreds of deltas per second. Those
notifications are state updates, not a request to reconcile the entire QWidget
tree at the same rate. The desktop pipeline therefore has two explicit lanes:

Structural lane
    item started/completed, snapshot reconciliation, turn completion, tool/diff
    insertion. These go through the normal keyed ``TranscriptView.render`` path
    where order, action bars and semantic presentation are allowed to change.

Streaming lane
    assistant text and process stdout/stderr deltas. State is accumulated
    immediately, then the GUI samples the latest state at one bounded frame rate
    and updates only widgets that already exist. It never runs semantic reorder,
    never moves transcript chrome and never forces the scrollbar.

This keeps model throughput independent from paint throughput and gives one
owner to each concern: ThreadState owns truth, the frame timer owns scheduling,
Message/Activity widgets own local content, and TranscriptView's rangeChanged
handler owns tail following.
"""

from __future__ import annotations

from typing import Any, Iterable

from PySide6.QtCore import QTimer

from app.desktop import output_presentation


# ~33 fps is visually continuous for text while leaving the GUI thread enough
# room for Markdown layout, input, scrolling and side-panel painting.
STREAM_FRAME_MS = 30

_VIEW_INSTALLED = False
_WINDOW_INSTALLED = False
_REVEAL_INSTALLED = False


def _update_existing_entries(view: Any, entries: Iterable[Any]) -> bool:
    """Update content/signatures in place without reconciling layout structure.

    Returns ``False`` when any entry has no existing widget; callers must fall
    back to a structural render in that case. The preflight happens before any
    mutation so a mixed batch can never be half-applied.
    """
    entries = list(entries)
    if not entries:
        return True

    widgets = getattr(view, "_widgets", None)
    signatures = getattr(view, "_signatures", None)
    apply_entry = getattr(view, "_apply", None)
    if not isinstance(widgets, dict) or not isinstance(signatures, dict) or not callable(apply_entry):
        return False
    if any(getattr(entry, "key", "") not in widgets for entry in entries):
        return False

    for entry in entries:
        key = getattr(entry, "key", "")
        signature = entry.signature()
        if signatures.get(key) == signature:
            continue
        apply_entry(widgets[key], entry)
        signatures[key] = signature
    return True


def _entries_for_keys(state: Any, keys: set[str]) -> list[Any]:
    """Materialise only dirty transcript entries, not the whole conversation."""
    if not keys:
        return []

    # ThreadState exposes these as internal building blocks so its ordinary
    # entries() API can stay simple. The stream lane is the one performance-
    # sensitive caller: flushing and constructing 1-2 dirty rows avoids walking
    # hundreds of historical transcript items 30+ times a second.
    flush = getattr(state, "_flush_stream_fragments", None)
    order = getattr(state, "_order", None)
    items = getattr(state, "_items", None)
    build = getattr(state, "_entry", None)
    if callable(flush) and isinstance(order, list) and isinstance(items, dict) and callable(build):
        flush(keys)
        wanted = {str(key) for key in keys}
        result = []
        for key in order:
            if key not in wanted:
                continue
            item = items.get(key)
            if item is None:
                continue
            entry = build(key, item)
            if entry is not None:
                result.append(entry)
        return result

    # Compatibility fallback for alternate/test state objects.
    return [entry for entry in state.entries() if getattr(entry, "key", "") in keys]


def _render_terminal_now(window: Any) -> None:
    terminal = getattr(window, "terminal_view", None)
    state = getattr(window, "state", None)
    if terminal is None or state is None:
        return
    # An inactive Runtime tab has no pixels on screen. Do not keep laying out 30
    # terminal cards for every stdout frame just in case the reader opens it.
    # Structural snapshots/completion refresh it, and a live process will refresh
    # it on the next frame immediately after the tab becomes visible.
    try:
        if not terminal.isVisible():
            return
    except (AttributeError, RuntimeError):
        pass
    terminal.render_items(state.items_of_type("process")[-30:])


def _discard_pending_frame(window: Any) -> None:
    timer = getattr(window, "_stream_frame_timer", None)
    if timer is not None:
        timer.stop()
    dirty = getattr(window, "_stream_dirty_keys", None)
    if isinstance(dirty, set):
        dirty.clear()


def _schedule_stream_frame(window: Any, key: str, *, terminal: bool = False) -> None:
    if key:
        window._stream_dirty_keys.add(str(key))
    if terminal:
        window._stream_terminal_dirty = True
    timer = window._stream_frame_timer
    if not timer.isActive():
        timer.start()


def _flush_stream_frame(window: Any) -> None:
    """Paint the newest accumulated stream state once."""
    if bool(getattr(window, "_closed", False)):
        _discard_pending_frame(window)
        return

    dirty_keys = set(getattr(window, "_stream_dirty_keys", set()))
    window._stream_dirty_keys.clear()
    terminal_dirty = bool(getattr(window, "_stream_terminal_dirty", False))
    window._stream_terminal_dirty = False

    if dirty_keys:
        entries = _entries_for_keys(window.state, dirty_keys)
        updater = getattr(window.transcript, "update_stream_entries", None)
        updated = bool(callable(updater) and updater(entries) and len(entries) == len(dirty_keys))
        if not updated:
            # Missing widget means this was actually a structural arrival (for
            # example a provider emitted a delta before item/started). Use the
            # full canonical render exactly once for that exceptional frame.
            window._stream_structural_render()

    if terminal_dirty:
        _render_terminal_now(window)


def install_disclosure_fast_path() -> None:
    """Do not measure hidden disclosure bodies on every output token.

    Importing transcript_disclosure is intentionally delayed until the desktop
    package has installed all presentation refinements. That preserves Loom's
    canonical class-construction order instead of accidentally defining final
    transcript widgets too early just to install a performance hook.
    """
    global _REVEAL_INSTALLED
    if _REVEAL_INSTALLED:
        return
    _REVEAL_INSTALLED = True

    from app.desktop import transcript_disclosure

    cls = transcript_disclosure.AnimatedReveal
    original_refresh = cls.refresh_target

    def refresh_target(self: Any) -> None:
        # set_expanded(True) flips _expanded before calling refresh_target, so a
        # body skipped while closed is measured with current content exactly
        # when the user opens it. Closed reasoning/tool output has zero geometry
        # and therefore needs no repeated document-size/layout measurement.
        if (
            not bool(getattr(self, "_expanded", False))
            and getattr(self, "_animation", None) is None
            and float(getattr(self, "_progress", 0.0) or 0.0) <= 0.001
        ):
            return
        original_refresh(self)

    cls.refresh_target = refresh_target


def install_view() -> None:
    """Expose a content-only update lane on every final transcript subclass."""
    global _VIEW_INSTALLED
    if _VIEW_INSTALLED:
        return
    _VIEW_INSTALLED = True

    def update_stream_entries(self: Any, entries: Iterable[Any]) -> bool:
        return _update_existing_entries(self, entries)

    # AnchoredTranscriptView inherits this method from the presentation class,
    # so stream frames never need a second reconciler or another render wrapper.
    output_presentation.TranscriptView.update_stream_entries = update_stream_entries


def install_window(window_cls: type[Any]) -> None:
    """Separate high-frequency deltas from structural transcript reconciliation."""
    global _WINDOW_INSTALLED
    if _WINDOW_INSTALLED:
        return
    _WINDOW_INSTALLED = True

    original_init = window_cls.__init__
    original_apply_item_delta = window_cls._apply_item_delta
    original_render_transcript = window_cls._render_transcript
    original_close_event = window_cls.closeEvent

    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._stream_dirty_keys: set[str] = set()
        self._stream_terminal_dirty = False
        self._stream_frame_timer = QTimer(self)
        self._stream_frame_timer.setSingleShot(True)
        self._stream_frame_timer.setInterval(STREAM_FRAME_MS)
        self._stream_frame_timer.timeout.connect(lambda: _flush_stream_frame(self))
        # Bypass the wrapper below only when a stream frame discovers a genuinely
        # missing widget and must promote itself to structural reconciliation.
        self._stream_structural_render = lambda: original_render_transcript(self)

    def render_transcript(self: Any) -> Any:
        # Any caller deliberately asking for a full transcript render supersedes
        # a pending sampled frame: ThreadState already contains every accumulated
        # delta, so the structural render paints the newest content itself.
        timer = getattr(self, "_stream_frame_timer", None)
        if timer is not None:
            timer.stop()
        dirty = getattr(self, "_stream_dirty_keys", None)
        if isinstance(dirty, set):
            dirty.clear()
        result = original_render_transcript(self)
        if bool(getattr(self, "_stream_terminal_dirty", False)):
            self._stream_terminal_dirty = False
            _render_terminal_now(self)
        return result

    def apply_item_delta(self: Any, params: dict[str, Any]) -> Any:
        delta = params.get("delta") if isinstance(params, dict) else None
        if not isinstance(delta, dict):
            return original_apply_item_delta(self, params)

        item_id = params.get("itemId")
        handled = False

        if "text" in delta:
            handled = True
            key = self.state.append_text_delta(item_id, delta.get("text"))
            if key:
                _schedule_stream_frame(self, key)

        stdout = str(delta.get("stdout") or "")
        stderr = str(delta.get("stderr") or "")
        if stdout or stderr:
            handled = True
            key = self.state.append_process_output(item_id, stdout=stdout, stderr=stderr)
            if key:
                _schedule_stream_frame(self, key, terminal=True)

        if handled:
            return None
        # Preserve future/unknown delta behavior from the window rather than
        # silently swallowing a protocol extension.
        return original_apply_item_delta(self, params)

    def close_event(self: Any, event: Any) -> Any:
        timer = getattr(self, "_stream_frame_timer", None)
        if timer is not None:
            timer.stop()
        return original_close_event(self, event)

    window_cls.__init__ = init
    window_cls._render_transcript = render_transcript
    window_cls._apply_item_delta = apply_item_delta
    window_cls.closeEvent = close_event


__all__ = [
    "STREAM_FRAME_MS",
    "_entries_for_keys",
    "_flush_stream_frame",
    "_update_existing_entries",
    "install_disclosure_fast_path",
    "install_view",
    "install_window",
]
