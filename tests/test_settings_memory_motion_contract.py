from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "desktop-react" / "src" / "App.tsx"
PAGE = ROOT / "desktop-react" / "src" / "components" / "SettingsPage.tsx"
MEMORY = ROOT / "desktop-react" / "src" / "components" / "SettingsMemoryBridge.tsx"
MOTION = ROOT / "desktop-react" / "src" / "components" / "settings-page-motion.css"


def test_memory_is_a_first_class_settings_route() -> None:
    app = APP.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")
    memory = MEMORY.read_text(encoding="utf-8")

    assert 'import { MemoryPanel } from "./SettingsMemoryBridge";' in page
    assert '| "memory"' in page
    assert '{ key: "memory", label: "Memory", icon: BrainCircuit }' in page
    assert 'if (page === "memory") return <MemoryPanel threadId={threadId} running={running} />;' in page
    assert 'threadId={thread?.id}' in app
    assert "SettingsMemoryBridge" not in app
    assert "export function MemoryPanel" in memory
    assert "createPortal" not in memory


def test_memory_uses_the_same_directional_page_motion_as_every_other_section() -> None:
    page = PAGE.read_text(encoding="utf-8")
    motion = MOTION.read_text(encoding="utf-8")

    assert '"skills", "memory", "permissions"' in page
    assert 'data-page={page}' in page
    assert 'data-page-motion={pageMotion}' in page
    assert 'data-page="memory"' in motion
    assert 'data-page-motion^="entering"' in motion
    assert "transition-delay: 34ms;" in motion
    assert "transition-delay: 52ms;" in motion
    assert "transition-delay: 70ms;" in motion


def test_settings_page_motion_is_compositor_only_and_directional() -> None:
    page = PAGE.read_text(encoding="utf-8")
    motion = MOTION.read_text(encoding="utf-8")

    assert "const SETTINGS_SECTION_EXIT_MS = 142;" in page
    assert 'data-page-motion="entering-forward"' in motion
    assert 'data-page-motion="entering-backward"' in motion
    assert 'data-page-motion="leaving-forward"' in motion
    assert 'data-page-motion="leaving-backward"' in motion
    assert "translate3d(12px, 6px, 0)" in motion
    assert "translate3d(-12px, 6px, 0)" in motion
    assert "filter:" not in motion
    assert "backdrop-filter:" not in motion


def test_settings_page_motion_respects_reduced_motion() -> None:
    motion = MOTION.read_text(encoding="utf-8")

    assert "@media (prefers-reduced-motion: reduce)" in motion
    assert 'html[data-loom-reduced-motion="true"] .settings-page-surface' in motion
