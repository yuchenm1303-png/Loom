from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "desktop-react" / "src" / "App.tsx"
PAGE = ROOT / "desktop-react" / "src" / "components" / "SettingsPage.tsx"
MEMORY = ROOT / "desktop-react" / "src" / "components" / "SettingsMemoryBridge.tsx"
MOTION = ROOT / "desktop-react" / "src" / "components" / "settings-page-motion.css"
MOTION_JS = ROOT / "desktop-react" / "src" / "components" / "settingsMotion.ts"


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


def test_memory_uses_the_same_page_entrance_as_every_other_section() -> None:
    page = PAGE.read_text(encoding="utf-8")
    motion = MOTION.read_text(encoding="utf-8")
    script = MOTION_JS.read_text(encoding="utf-8")

    assert 'data-page={page}' in page
    # Memory (like models, web search and connectors) wraps its blocks in one
    # container of its own; the shared entrance steps inside it, so no page needs
    # its own delays or selectors.
    assert 'top.length === 1 && !top[0].classList.contains("settings-page-heading")' in script
    assert "PAGE_FLOW_LADDER_MS" in script
    assert 'data-page="memory"' not in motion
    assert "transition-delay: 34ms;" not in motion


def test_settings_page_motion_keeps_readable_blocks_stationary() -> None:
    page = PAGE.read_text(encoding="utf-8")
    motion = MOTION.read_text(encoding="utf-8")

    # The destination commits immediately; readable page blocks fade in place.
    assert "SETTINGS_SECTION_EXIT_MS" not in page
    assert "@keyframes settings-block-fade" in motion
    assert "@keyframes settings-block-rise" not in motion
    assert "@keyframes settings-block-fall" not in motion
    assert "filter:" not in motion
    assert "backdrop-filter:" not in motion


def test_settings_page_motion_respects_reduced_motion() -> None:
    motion = MOTION.read_text(encoding="utf-8")

    assert "@media (prefers-reduced-motion: reduce)" in motion
    assert (
        'html[data-loom-reduced-motion="true"] .settings-shell.settings-refined '
        '.settings-page-surface[data-flow] [data-flow-block]'
    ) in motion
