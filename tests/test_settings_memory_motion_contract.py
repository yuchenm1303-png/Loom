from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "desktop-react" / "src" / "components" / "SettingsMemoryBridge.tsx"
CSS = ROOT / "desktop-react" / "src" / "components" / "settings-memory.css"


def test_memory_settings_uses_shared_motion_presence_contract() -> None:
    source = BRIDGE.read_text(encoding="utf-8")

    assert 'import { useMotionPresence } from "../motion/useMotionPresence";' in source
    assert "const contentPresence = useMotionPresence(open, 260);" in source
    assert "contentPresence.mounted" in source
    assert 'className="settings-memory-motion-surface"' in source
    assert "data-motion-phase={contentPresence.phase}" in source


def test_memory_settings_entry_is_compositor_only_and_staggered() -> None:
    css = CSS.read_text(encoding="utf-8")

    entering = css[
        css.index('.settings-memory-motion-surface[data-motion-phase="entering"] {'):
        css.index('.settings-memory-motion-surface[data-motion-phase="entered"] {')
    ]
    assert "translate3d" in entering
    assert "scale(.996)" in entering
    assert "will-change: opacity, transform;" in entering

    entered = css[
        css.index('.settings-memory-motion-surface[data-motion-phase="entered"] {'):
        css.index('.settings-memory-motion-surface[data-motion-phase="exiting"] {')
    ]
    assert "opacity 260ms" in entered
    assert "transform 340ms" in entered

    assert "transition-delay: 24ms;" in css
    assert "transition-delay: 48ms;" in css
    assert "transition-delay: 72ms;" in css


def test_memory_settings_motion_respects_reduced_motion() -> None:
    css = CSS.read_text(encoding="utf-8")

    assert "@media (prefers-reduced-motion: reduce)" in css
    assert 'html[data-loom-reduced-motion="true"] .settings-memory-motion-surface' in css
