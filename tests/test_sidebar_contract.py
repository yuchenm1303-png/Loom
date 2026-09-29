import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"
SIDEBAR_CSS = COMPONENTS / "sidebar.css"

# The sidebar used to be restyled by four late override files plus sidebar
# rules in theme.css, styles.css, typography-scale.css and
# renderer-crispness.css, with !important and flex `order` fighting over the
# result. Its classes now have one stylesheet. (The panel's placement and
# open/close motion stay in workspace-panels.css and are keyed on `.sidebar`.)
SIDEBAR_SELECTORS = re.compile(
    r"\.compact-(?:sidebar|search|thread)"
    r"|\.codex-sidebar"
    r"|\.sidebar-(?:new-conversation|secondary-action|section|notice)"
    r"|\.project-(?:group|disclosure|rename-input)"
    r"|\.project-thread-list(?!-panel)"
    r"|\.thread-(?:quick-actions|context-menu|copy-submenu|project-submenu|menu-separator|project-remove)"
    r"|\.archive-count|\.workspace-group|\.recent-thread-list"
)
RETIRED_LAYERS = (
    "sidebar-codex-polish",
    "sidebar-primary-actions-polish",
    "sidebar-clarity-fix",
    "sidebar-project-actions",
)
CJK = re.compile(r"[㐀-鿿＀-￯]")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def rules(path: Path) -> str:
    """Stylesheet text without comments, which may name other files' selectors."""
    return re.sub(r"/\*.*?\*/", "", read(path), flags=re.S)


def rule_body(css: str, selector: str) -> str:
    start = css.index(selector + " {")
    return css[start:css.index("}", start)]


def test_sidebar_classes_have_one_stylesheet() -> None:
    styled_by = sorted(path.name for path in SRC.rglob("*.css") if SIDEBAR_SELECTORS.search(rules(path)))

    assert styled_by == ["sidebar.css"]


def test_retired_override_layers_stay_gone() -> None:
    sources = "".join(read(SRC / name) for name in ("main.tsx", "App.tsx", "components/Sidebar.tsx"))

    for name in RETIRED_LAYERS:
        assert name not in sources
        assert not (COMPONENTS / f"{name}.css").exists()


def test_closed_search_takes_no_space() -> None:
    # A definite height on the grid item becomes the 0fr row's minimum, which
    # left a blank band under the primary actions while search was closed.
    body = rule_body(rules(SIDEBAR_CSS), ".compact-search")

    assert re.search(r"(?<!min-)height:", body) is None


def test_conversation_list_keeps_the_thin_custom_scrollbar() -> None:
    # Any non-auto scrollbar-width/-color (the global `*` rules set both)
    # switches Chromium to Windows' native scrollbar with arrow buttons.
    body = rule_body(rules(SIDEBAR_CSS), ".sidebar.compact-sidebar .compact-thread-scroll")

    assert "scrollbar-width: auto;" in body
    assert "scrollbar-color: auto;" in body


def test_list_tracks_cannot_outgrow_the_panel() -> None:
    body = rule_body(rules(SIDEBAR_CSS), ".workspace-thread-list")

    assert "grid-template-columns: minmax(0, 1fr);" in body


def test_sidebar_text_uses_whole_pixels() -> None:
    # renderer-crispness.css: fractional sizes soften glyphs on Windows.
    sizes = re.findall(r"font-size:\s*([^;]+);", rules(SIDEBAR_CSS))

    assert sizes
    assert [size for size in sizes if not re.fullmatch(r"\d+px", size.strip())] == []


def test_sidebar_copy_follows_the_interface_language() -> None:
    source = read(COMPONENTS / "Sidebar.tsx")
    start = source.index("const SIDEBAR_COPY = {")
    end = source.index("} as const;", start)
    table = source[start:end]
    outside = source[:start] + source[end:]

    assert CJK.search(outside) is None
    english, chinese = table.split("\n  zh: {")
    keys = lambda block: re.findall(r"^    (\w+):", block, flags=re.M)
    assert keys(english) == keys(chinese)


def test_projects_come_before_recent_without_css_reordering() -> None:
    source = read(COMPONENTS / "Sidebar.tsx")

    assert source.index("{renderProjects()}") < source.index("{renderRecent()}")
    assert re.search(r"(?<![-\w])order:", rules(SIDEBAR_CSS)) is None


def test_conversation_rows_open_only_their_own_menu() -> None:
    sidebar = read(COMPONENTS / "Sidebar.tsx")
    global_menu = read(COMPONENTS / "GlobalContextMenu.tsx")

    assert 'data-loom-own-menu=""' in sidebar
    assert '[data-loom-own-menu]' in global_menu
