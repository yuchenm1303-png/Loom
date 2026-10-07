import re
from ui_stylesheet_ownership import owns_component_layout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"

# The starter cards used to be styled by five stacked stylesheets (styles.css,
# theme.css, typography-scale.css and three starter-card-*.css override layers);
# what rendered was whichever rule won last. Each part now has one owner.
OWNERS = {
    "conversation-home.css": re.compile(
        r"\.(?:starter-(?:grid|card|icon|content|arrow)|empty-intro|empty-state)\b"
        r"|\.transcript(?:-scroll)?\.is-empty\b"
    ),
    "home-token-activity.css": re.compile(r"\.home-token-"),
}
RETIRED_LAYERS = (
    "starter-cards-polish",
    "starter-card-interaction-glow",
    "starter-card-borderless-refinement",
)
# typography-scale.css sets the app's readability floor for secondary text.
MICRO_FLOOR_PX = 11.5


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def rules(path: Path) -> str:
    """Stylesheet text without comments, which may name other files' selectors."""
    return re.sub(r"/\*.*?\*/", "", read(path), flags=re.S)


def test_each_home_part_has_one_stylesheet_owner() -> None:
    for owner, selectors in OWNERS.items():
        styled_by = sorted(path.name for path in SRC.rglob("*.css") if owns_component_layout(path, selectors, rules(path)))

        assert styled_by == [owner]


def test_retired_override_layers_stay_gone() -> None:
    main = read(SRC / "main.tsx")
    assert 'import "./renderer-styles";' in main
    cascade = read(SRC / "renderer-styles.ts")

    assert 'import "./components/conversation-home.css";' in cascade
    for name in RETIRED_LAYERS:
        assert name not in main and name not in cascade
        assert not (COMPONENTS / f"{name}.css").exists()


def test_home_text_stays_at_the_readability_floor() -> None:
    for name in OWNERS:
        for value in re.findall(r"font-size:\s*([^;]+);", rules(COMPONENTS / name)):
            value = value.strip()
            if value.startswith("var(--loom-font-"):
                continue
            assert value.endswith("px"), f"{name}: {value}"
            assert float(value[:-2]) >= MICRO_FLOOR_PX, f"{name}: {value}"


def test_heatmap_renders_only_whole_weeks() -> None:
    activity = read(COMPONENTS / "HomeTokenActivity.tsx")

    # The grid is sliced to the weeks that fit rather than clipped by overflow,
    # so no column or month label is ever cut in half at the left edge.
    assert "new ResizeObserver(measure)" in activity
    assert ".slice(-capacity)" in activity
    assert "marker.column >= FIRST_LABEL_COLUMN" in activity


def test_starters_follow_the_interface_language() -> None:
    transcript = read(COMPONENTS / "Transcript.tsx")
    start = transcript.index("const starterPrompts = [")
    starters = transcript[start:transcript.index("] as const;", start)]

    # Title, copy and the prompt that is sent are all [English, Chinese] pairs.
    assert starters.count("title: [") == starters.count("copy: [") == starters.count("prompt: [") == 4
    assert "onPrompt?.(prompt[lang])" in transcript
    assert "empty-kicker" not in transcript
    assert "starter-eyebrow" not in transcript
