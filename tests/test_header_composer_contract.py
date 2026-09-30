import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"

# The header was styled by thread-header.css, thread-review-entry.css,
# thread-header-mark-refinement.css, account-auth.css, workspace-panels.css,
# theme.css, styles.css and typography-scale.css; the composer by composer.css
# plus three override layers, theme.css, styles.css and typography-scale.css.
# Each now has one stylesheet. Rules that only animate or theme-transition
# these elements, the model picker's own popover, and generic SVG hygiene are
# not styling ownership and are ignored below.
HEADER_SELECTORS = re.compile(
    r"\.polished-thread-header|\.thread-header-(?:leading|main|copy|divider|icon-button)\b"
    r"|\.thread-title\b|\.workspace-(?:path-button|leaf|copy-icon)"
    r"|\.thread-status-(?:chip|orb|label)|\.thread-(?:review|agent|artifact)-(?:button|count)"
    r"|\.thread-account-(?:initial|dot)|\.panel-toggle-button"
)
COMPOSER_SELECTORS = re.compile(
    r"\.composer(?![\w-])|\.composer-(?!attachment|quote|stage)[a-z-]+"
    r"|\.send-button|\.permission-(?:popover|option|badge|footnote)|\.model-chip|\.sticker-chip"
)
NOT_OWNERSHIP = re.compile(r"loom-panel-motion|loom-theme-transitioning|model-manager-popover|\.settings-shell svg")
RETIRED_LAYERS = (
    "thread-review-entry",
    "thread-header-mark-refinement",
    "composer-control-pills",
    "composer-stability",
    "permission-popover-polish",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def styling_rules(path: Path) -> str:
    """Rule text without comments, :not() arguments or non-ownership rules."""
    css = re.sub(r"/\*.*?\*/", "", read(path), flags=re.S)
    css = re.sub(r":not\([^()]*\)", "", css)
    return "".join(
        rule for rule in re.findall(r"[^{}]*\{[^{}]*\}", css)
        if not NOT_OWNERSHIP.search(rule.split("{")[0])
    )


def owners(selectors: re.Pattern[str]) -> list[str]:
    return sorted(path.name for path in SRC.rglob("*.css") if selectors.search(styling_rules(path)))


def test_header_has_one_stylesheet() -> None:
    assert owners(HEADER_SELECTORS) == ["thread-header.css"]


def test_composer_has_one_stylesheet() -> None:
    assert owners(COMPOSER_SELECTORS) == ["composer.css"]


def test_retired_override_layers_stay_gone() -> None:
    sources = "".join(read(path) for path in SRC.rglob("*.tsx"))

    for name in RETIRED_LAYERS:
        assert name not in sources
        assert not (COMPONENTS / f"{name}.css").exists()


def test_header_height_is_one_token_shared_by_every_header_row() -> None:
    styles = read(SRC / "styles.css")

    assert "--loom-header-height: 52px;" in styles
    assert "grid-template-rows: var(--loom-header-height) minmax(0, 1fr) auto;" in styles
    assert "height: var(--loom-header-height, 52px);" in read(COMPONENTS / "thread-header.css")
    assert "grid-template-rows: var(--loom-header-height, 52px) 57px minmax(0, 1fr);" in read(COMPONENTS / "Inspector.css")
    # A second fixed row height let the 68px header overlap the conversation.
    for path in SRC.rglob("*.css"):
        for body in re.findall(r"\.workspace\s*\{([^}]*)\}", read(path)):
            assert "grid-template-rows" not in body or "--loom-header-height" in body, path.name


def test_conversation_column_shares_the_composer_axis() -> None:
    styles = read(SRC / "styles.css")
    start = styles.index(".transcript-scroll {")
    rule = styles[start:styles.index("}", start)]

    assert "scrollbar-gutter: stable both-edges;" in rule
    assert "--home-width: var(--content-width, 860px);" in read(COMPONENTS / "conversation-home.css")


def test_chrome_shares_one_token_set() -> None:
    theme = read(SRC / "theme.css")

    for token in ("--loom-ink:", "--loom-ink-3:", "--loom-hover:", "--loom-selected:", "--loom-accent-rgb:"):
        assert theme.count(token) == 2, token  # dark :root and the light theme
    for name in ("sidebar.css", "thread-header.css", "composer.css"):
        css = read(COMPONENTS / name)
        assert "var(--loom-ink" in css and "var(--loom-hover)" in css, name


def test_composer_markup_carries_no_hidden_decorations() -> None:
    markup = read(COMPONENTS / "ComposerBase.tsx") + read(COMPONENTS / "Composer.tsx")

    for retired in ("composer-glow", "composer-spark", "composer-keycap", "composer-divider", "composer-running-label"):
        assert retired not in markup


def test_header_shows_status_only_when_it_is_news() -> None:
    header = read(COMPONENTS / "ThreadHeader.tsx")

    assert 'state.tone !== "ready" ?' in header
    assert "thread-state-dot" not in header
    assert "thread-review-entry.css" not in header
