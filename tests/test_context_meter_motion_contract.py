from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METER = ROOT / "desktop-react" / "src" / "components" / "ContextMeter.tsx"
METER_CSS = ROOT / "desktop-react" / "src" / "components" / "context-meter.css"
HEADER_CSS = ROOT / "desktop-react" / "src" / "components" / "thread-header.css"


def test_context_meter_exit_has_headroom_before_presence_unmount() -> None:
    source = METER.read_text(encoding="utf-8")
    css = METER_CSS.read_text(encoding="utf-8")

    assert "useMotionPresence(open, RETAIN.fast)" in source
    exit_start = css.index('.context-meter-panel[data-motion-phase="exiting"] {')
    exit_end = css.index('}', exit_start)
    exit_rule = css[exit_start:exit_end]
    # The panel fades like every popover: opacity alone, on the fast exit.
    assert "opacity var(--loom-dur-fast-out)" in exit_rule
    assert "transform" not in exit_rule


def test_context_meter_exit_is_compositor_owned_not_inset_animated() -> None:
    css = METER_CSS.read_text(encoding="utf-8")

    exit_start = css.index('.context-meter-panel[data-motion-phase="exiting"] {')
    exit_end = css.index('}', exit_start)
    exit_rule = css[exit_start:exit_end]

    assert "translate3d" not in exit_rule
    assert "will-change: opacity;" in exit_rule
    # The fixed component surface replaces the previously overridden pseudo-shell.
    assert ".context-meter-panel::before" not in css
    assert "transition-delay:" not in css
    assert "context-meter-panel" not in (ROOT / "desktop-react/src/shell-fix.css").read_text(encoding="utf-8")


def test_generic_header_popover_motion_does_not_double_animate_context_meter() -> None:
    css = HEADER_CSS.read_text(encoding="utf-8")

    assert '[role="dialog"]:not(.context-meter-panel)' in css
    assert '[role="menu"]:not(.context-meter-panel)' in css
    generic_start = css.index('/* Header context popover layering + motion')
    generic = css[generic_start:]
    assert '.context-meter-panel)' in generic
