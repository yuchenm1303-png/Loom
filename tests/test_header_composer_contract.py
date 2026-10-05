from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_header_uses_explicit_glass_style_layers() -> None:
    header = read(COMPONENTS / "ThreadHeader.tsx")
    assert 'import "./thread-header.css";' in header
    assert 'className="thread-header polished-thread-header"' in header
    assert (COMPONENTS / "thread-header.css").is_file()


def test_composer_keeps_core_stylesheet_and_editable_steering_surface() -> None:
    composer = read(COMPONENTS / "Composer.tsx")
    assert 'import "./composer.css";' in composer
    assert "composer is-steering" in composer
    assert "composer-input-row" in composer
    assert "disabled={stopping}" in composer


def test_header_status_is_visible_and_state_driven() -> None:
    header = read(COMPONENTS / "ThreadHeader.tsx")
    assert 'tone: "ready"' in header
    # 3b8c8a00 removed the duplicate title dot; the status chip owns this state.
    assert "thread-state-dot" not in header
    assert 'className={`thread-status-chip ${state.tone}`}' in header
    assert 'className="thread-status-label">{state.label}' in header
    assert "thread-status-chip" in header
    assert 'status === "waiting_approval"' in header


def test_conversation_surface_remains_a_scroll_container() -> None:
    styles = read(SRC / "styles.css")
    start = styles.index(".transcript-scroll {")
    rule = styles[start:styles.index("}", start)]
    assert "overflow-y: auto" in rule
    assert "overflow-x: hidden" in rule


def test_theme_keeps_explicit_light_and_dark_surfaces() -> None:
    combined = read(SRC / "styles.css") + read(SRC / "theme.css")
    assert "color-scheme: dark" in combined
    assert 'data-loom-theme="light"' in combined


def test_glass_decorations_are_non_interactive() -> None:
    composer = read(COMPONENTS / "Composer.tsx")
    assert 'className="composer-glow" aria-hidden="true"' in composer
    assert 'className="composer-spark" aria-hidden="true"' in composer
