"""Contracts for the panel language (desktop-react/src/components/panel-language.css).

Every panel Loom opens shares one look and one motion. These tests pin the parts that
cannot be seen from a screenshot: who owns what, and what was deleted so that it cannot
grow back. The behaviour itself is checked in a real browser by
desktop-react/scripts/tests/panel-language-browser.mjs.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"


def read(path: str) -> str:
    return (SRC / path).read_text(encoding="utf-8")


def css_block(css: str, selector: str) -> str:
    start = css.index(selector + " {")
    return css[start:css.index("}", start) + 1]


def test_kit_loads_after_the_component_sheets_and_before_the_window_rail() -> None:
    order = re.findall(r'import "\./([^"]+\.css)";', read("renderer-styles.ts"))

    assert "components/panel-language.css" in order
    # The kit is the shared owner, so it must win ties against the component polish layers,
    # while window-rail.css keeps the last word on how headers leave room for the controls.
    assert order.index("components/panel-language.css") > order.index("components/header-action-family.css")
    assert order.index("components/panel-language.css") < order.index("components/window-rail.css")
    # Merged into the kit; a second owner of the dock fallback would bring the drift back.
    assert not (COMPONENTS / "review-motion.css").exists()
    assert "review-motion.css" not in read("renderer-styles.ts")


def test_tokens_exist_in_both_themes_and_ride_the_workspace_palette() -> None:
    css = read("components/panel-language.css")
    root = css_block(css, ":root")
    light = css_block(css, 'html[data-loom-theme="light"]')

    for token in (
        "surface", "surface-raised", "card", "field", "well", "edge", "line", "hover", "selected",
        "ink", "muted", "accent", "accent-wash", "focus", "scrim", "shadow-float", "shadow-modal",
        "radius-control", "radius-card", "radius-float", "radius-modal", "header-height", "mark",
    ):
        assert f"--pl-{token}:" in root, token
    # What the theme already knows is referenced, not copied: the light theme remaps it for free.
    assert "--pl-surface: var(--loom-workspace-navigation" in root
    assert "--pl-surface-raised: var(--loom-workspace-panel" in root
    assert "--pl-ink: var(--loom-workspace-ink" in root
    assert "--pl-ok: var(--semantic-success-strong" in root
    assert "--pl-danger: var(--semantic-danger-text" in root
    # Only what a token cannot express is remapped.
    for token in ("card", "field", "well", "edge", "scrim", "shadow-float", "shadow-modal"):
        assert f"--pl-{token}:" in light, token
    # The rail height exists only in the desktop shell; the web build must still get a header.
    assert "--pl-header-height: var(--loom-rail-height, 60px)" in root


def test_float_shell_is_shared_by_the_composer_panels_and_the_menus() -> None:
    kit = read("components/panel-language.css")
    panels = read("components/composer-panels.css")
    refined = read("components/composer-refined.css")

    # One quiet surface for everything that hangs off a control: a 13px corner and one long soft shadow.
    # The composer's panels take the float tokens (composer-panels.css), the context menus the same shell here.
    assert "--pl-radius-float: 13px" in kit
    assert "--pl-shadow-float: 0 1px 2px rgba(0, 0, 0, .35), 0 26px 64px -20px rgba(0, 0, 0, .78)" in kit
    assert "--cq-shadow-pop: 0 1px 2px rgba(0, 0, 0, .35), 0 26px 64px -20px rgba(0, 0, 0, .78)" in refined
    assert "--cp-shadow: var(--pl-shadow-float" in panels and "--cp-radius: var(--pl-radius-float" in panels
    assert ":root:root .loom-context-menu {" in kit
    assert "model-manager-popover" not in kit


def test_every_dock_is_a_dock_with_the_shared_header() -> None:
    docks = {
        "components/ReviewWorkspace.tsx": 'className="review-workspace pl-dock"',
        "components/SubAgentDock.tsx": 'className="sub-agent-dock pl-dock"',
        "components/ArtifactPreviewDock.tsx": 'className="artifact-preview-dock pl-dock"',
        "components/ProjectDetailsPanel.tsx": 'className="project-details-panel pl-dock"',
        "components/Inspector.tsx": 'className="inspector runtime-inspector pl-dock"',
    }
    for path, marker in docks.items():
        assert marker in read(path), path

    # One header component: the dock headers, the palette's and the workbench's.
    for path in (
        "components/ReviewWorkspace.tsx", "components/SubAgentWorkspace.tsx", "components/ArtifactPreviewDock.tsx",
        "components/ProjectDetailsPanel.tsx", "components/BackgroundTaskPanel.tsx", "components/FilesWorkbench.tsx",
    ):
        assert "<PanelHeader" in read(path), path

    # The legacy hook classes stay on the header: the window rail cuts its buttons out of the drag region by them.
    rail = read("components/window-rail.css")
    assert ".pl-header" in rail
    assert ":root.loom-desktop-chrome .pl-dock .pl-header" in rail
    assert 'closeClassName="review-close"' in read("components/ReviewWorkspace.tsx")
    assert 'closeClassName="sub-agent-workspace-close"' in read("components/SubAgentWorkspace.tsx")
    assert 'closeClassName="project-details-close"' in read("components/ProjectDetailsPanel.tsx")


def test_the_headers_each_dock_used_to_draw_for_itself_are_gone() -> None:
    gone = {
        "components/review-workspace.css": (".review-heading", ".review-close", ".review-topbar {"),
        "components/review-dock.css": (".review-heading", ".review-topbar {"),
        "components/sub-agent-workspace.css": (
            ".sub-agent-workspace-header", ".sub-agent-workspace-copy", ".sub-agent-workspace-summary",
            ".sub-agent-workspace-mark", ".sub-agent-workspace-close", ".sub-agent-workspace-pulse",
        ),
        "components/project-details-panel.css": (".project-details-header", ".project-details-title", ".project-details-mark"),
        "components/artifact-preview-dock.css": (".artifact-preview-header", ".artifact-preview-heading", ".artifact-preview-mark"),
        "components/files-workbench.css": (".files-workbench-header", ".files-workbench-brand"),
        "components/background-tasks.css": (".background-task-topbar", ".background-task-heading", ".background-task-actions"),
        "theme.css": (".review-heading", ".review-topbar", ".runtime-title-row strong", ".runtime-health", ".runtime-close"),
    }
    for path, selectors in gone.items():
        css = read(path)
        for selector in selectors:
            assert selector not in css, f"{path} still styles {selector}"

    # The dock surfaces are drawn once. A second background on the root is how a panel got its own grey.
    for path, selector in (
        ("components/sub-agent-dock.css", ".sub-agent-dock"),
        ("components/artifact-preview-dock.css", ".artifact-preview-dock"),
        ("components/project-details-panel.css", ".project-details-panel"),
        ("components/review-workspace.css", ".review-workspace"),
    ):
        block = css_block(read(path), selector)
        assert "background" not in block and "box-shadow" not in block and "border-left" not in block, path


def test_nothing_that_arrives_in_place_is_unmounted_in_one_frame() -> None:
    background = read("components/BackgroundTaskPanel.tsx")
    files = read("components/FilesWorkbench.tsx")
    app = read("App.tsx")
    account = read("components/AccountDialog.tsx")
    crop = read("components/AvatarCropDialog.tsx")

    # Background tasks and Files used to disappear in one frame; both are held for their exit now.
    assert "useMotionPresence(open, RETAIN.base)" in background
    assert "if (!presence.mounted) return null;" in background
    assert "data-motion-phase={presence.phase}" in background
    assert "const filesPresence = useMotionPresence(Boolean(filesProject), RETAIN.base)" in app
    assert "lastFilesProjectRef" in app and "phase={filesPresence.phase}" in app
    assert 'data-motion-phase={phase}' in files and "if (!open) return null;" not in files
    # The crop dialog leaves like the account dialog it sits in, still showing its picture.
    assert "const cropPresence = useMotionPresence(Boolean(avatarCropFile), RETAIN.base)" in account
    assert "lastCropFileRef" in account and "phase={cropPresence.phase}" in account
    assert "data-motion-phase={phase}" in crop

    # Scrim and card are one element and fade together.
    for source in (files, account, crop):
        assert "pl-scrim pl-reveal" in source
    assert "pl-float pl-reveal" in background
    assert "pl-modal" in files and "pl-modal" in crop


def test_presence_is_held_longer_than_the_conceal_it_waits_for() -> None:
    tokens = read("motion-tokens.css")
    conceal = int(re.search(r"--loom-dur-base-out: (\d+)ms", tokens).group(1))
    reveal = int(re.search(r"--loom-dur-base: (\d+)ms", tokens).group(1))
    assert conceal < reveal

    # Every in-place surface is held by one named constant: the exit it waits for plus a margin (motion/tokens.ts).
    script = read("motion/tokens.ts")
    margin = int(re.search(r"RETAIN_MARGIN_MS = (\d+)", script).group(1))
    assert "base: DUR_OUT.base + RETAIN_MARGIN_MS" in script
    # The surface is still painted for the whole of its exit (a frame of slack at 60 Hz and more).
    assert margin >= 20
    for path, call in (
        ("components/BackgroundTaskPanel.tsx", "useMotionPresence(open, RETAIN.base)"),
        ("components/AccountDialog.tsx", "useMotionPresence(open, RETAIN.base)"),
        ("components/ImageLightbox.tsx", "useMotionPresence(open, RETAIN.base)"),
        ("App.tsx", "filesPresence = useMotionPresence(Boolean(filesProject), RETAIN.base)"),
        ("components/AccountDialog.tsx", "cropPresence = useMotionPresence(Boolean(avatarCropFile), RETAIN.base)"),
        ("App.tsx", "profilePresence = useMotionPresence(profileOpen, RETAIN.base)"),
        ("App.tsx", "settingsPresence = useMotionPresence(settingsOpen, RETAIN.base)"),
    ):
        assert call in read(path), (path, call)


def test_text_that_stays_in_place_is_revealed_by_opacity_alone() -> None:
    # The product's own rule since 23dcdb1c: a surface that appears in place and carries text does
    # not slide or scale (it shimmers on a moving layer); movement is for text-free marks and the
    # docks' a61 depth. Files, the palette, the crop dialog and the profile page used to break it.
    kit = read("components/panel-language.css")
    reveal = css_block(kit, ".pl-reveal")
    assert "transform" not in reveal
    assert "var(--loom-dur-base)" in reveal and "var(--loom-ease-out)" in reveal
    exiting = css_block(kit, '.pl-reveal[data-motion-phase="exiting"]')
    assert "var(--loom-dur-base-out)" in exiting and "var(--loom-ease-exit)" in exiting
    # Where it comes from is its starting style: it mounts straight into its entrance.
    assert '@starting-style {\n  .pl-reveal[data-motion-phase="entered"] {\n    opacity: 0;' in kit

    for path in (
        "components/files-workbench.css", "components/background-tasks.css", "components/avatar-crop.css",
        "components/image-lightbox.css", "components/account-auth.css",
    ):
        css = read(path)
        assert not re.search(r"@keyframes [\w-]*(arrive|enter|dialog-in|backdrop-in)[\w-]* \{[^}]*transform", css), path
        assert "translateY(8px)" not in css and "scale(.985)" not in css, path

    profile = read("components/profile-insights.css")
    page = css_block(profile, ".profile-insights-page")
    assert "transform" not in page and "opacity" in page
    # The profile canvas stays opaque like the settings host's: fading it shows the app through the page.
    assert "transition: background-color var(--loom-dur-fast) ease;" in css_block(profile, ".profile-insights-host")

    gm = read("global-motion.css")
    assert "opacity var(--loom-dur-base) var(--loom-ease-out)" in gm
    assert "transition-duration: var(--loom-dur-base-out)" in gm


def test_a61_dock_depth_is_still_the_only_dock_entrance() -> None:
    panels = read("components/workspace-panels.css")
    kit = read("components/panel-language.css")

    # Pinned by test_panel_enter_motion_contract: the sidebar and the inspector arrive with the a61 depth, live; the
    # portal docks say the same once, in the kit, through their presence (a starting style, then fast-out).
    assert "transform: translate3d(8px, 0, 0) scale(.992)" in panels
    assert "--loom-ease-out: cubic-bezier(.16, .78, .18, 1)" in read("motion-tokens.css")
    assert kit.count('@starting-style {\n  .pl-dock[data-motion-phase="entered"]') == 1
    assert 'data-motion-phase="entering"' not in kit
    for path in (
        "components/sub-agent-dock.css", "components/artifact-preview-dock.css",
        "components/project-details-panel.css", "components/review-workspace.css", "components/review-dock.css",
    ):
        assert 'data-motion-phase="entering"' not in read(path), path


def test_files_and_browser_follow_the_product_accent_and_the_theme() -> None:
    # Both were neutral grey with a blue accent and no light theme at all.
    for path in ("components/files-workbench.css", "components/browser-workspace.css"):
        css = read(path)
        assert 'html[data-loom-theme="light"]' not in css, f"{path}: the theme comes from the tokens"
        for blue in ("#388cf4", "#3f97f7", "#398cf0", "#4399fb", "#243b53", "#113353", "#80b6f9", "#263f5b", "#171819"):
            assert blue not in css.lower(), f"{path} still uses {blue}"
        assert "var(--pl-" in css


def test_empty_panels_share_one_still_state() -> None:
    kit = read("components/panel-language.css")
    start = kit.index("/* Empty state (PanelEmpty.tsx)")
    empty = kit[start:kit.index(".pl-card {", start)]

    # A still tile in one fading frame: an empty panel asks for no frame while the run streams.
    assert "animation" not in empty and "@keyframes" not in empty
    for part in (".pl-empty-art::before", ".pl-empty-core", ".pl-empty-title", ".pl-empty-copy", ".pl-empty-chips"):
        assert part in empty, part
    # No orbit, ring or satellite dot is left anywhere, the inspector's included: it draws the same art.
    for path in ("components/PanelEmpty.tsx", "components/panel-language.css", "components/Inspector.tsx",
                 "components/InspectorMark.css", "components/Inspector.css", "components/runtime-controls-polish.css"):
        assert "orbit" not in read(path).replace("Nothing orbits", ""), path
    assert '"pl-empty-art runtime-empty-visual"' in read("components/Inspector.tsx")
    for path in ("components/SubAgentWorkspace.tsx", "components/ReviewWorkspace.tsx", "components/FilesWorkbench.tsx"):
        assert "<PanelEmpty" in read(path), path
    # The orbit the agent panel used to draw for itself is gone from both the markup and the sheet.
    assert "sub-agent-empty" not in read("components/SubAgentWorkspace.tsx")
    assert "sub-agent-empty" not in read("components/sub-agent-workspace.css")
    # The artifact library keeps its own markup; its glyph takes the same 42px core.
    assert ".artifact-library-empty > svg" in kit


def test_cards_inside_the_docks_share_one_corner() -> None:
    kit = read("components/panel-language.css")
    registry = kit[kit.index(":root:root:root :is(\n  .project-stage-section,\n"):]
    registry = registry[:registry.index("}")]

    assert "--pl-radius-card" in registry
    for card in (".project-stage-section", ".project-commit-box", ".sub-agent-card"):
        assert card in registry, card
    # The project panel's parts are sections parted by a hairline, not cards; its small facts (the overview and
    # the Git tiles) take the card corner on a tone, without a frame.
    sections = kit[kit.index(":root:root:root :is(\n  .project-workspace-card,\n"):]
    sections = sections[:sections.index("}")]
    for section in (
        ".project-workspace-card", ".project-instructions-card", ".project-thread-card",
        ".project-memory-card", ".project-agent-files-card", ".project-repository-card",
    ):
        assert section in sections, section
    assert "border-top: 1px solid var(--pl-edge);" in sections and "border-radius: 0;" in sections
    assert "background: transparent;" in sections
    tiles = kit[kit.index(":root:root:root :is(.project-overview-card, .project-workspace-stat) {"):]
    tiles = tiles[:tiles.index("}")]
    assert "border: 0;" in tiles and "border-radius: var(--pl-radius-card);" in tiles


def test_dock_content_is_one_card_one_chip_one_segmented_control() -> None:
    kit = read("components/panel-language.css")

    # The card registry carries the material, not just the corner; the agent's avatar, status and the two
    # segmented controls (agent filter, inspector tabs) take the same tokens, with one slab token.
    assert "--pl-slab:" in kit
    for selector in (
        ":root:root:root .sub-agent-status", ":root:root:root .sub-agent-avatar", ":root:root:root .sub-agent-filter button.active",
        ":root:root:root .runtime-tab-glider", ":root:root:root :is(.project-overview-card, .project-workspace-stat)",
    ):
        assert selector in kit, selector
    card = kit[kit.index(":root:root:root :is(\n  .project-stage-section,\n"):]
    card = card[:card.index("\n}\n")]
    assert "background: var(--pl-card)" in card and "border: 1px solid var(--pl-line)" in card and "box-shadow: none" in card

    # The agent panel is as wide as its dock (a toolbar that cannot shrink once widened the column), its
    # one-line status note drops out of a narrow dock, and its header says one thing.
    agents = read("components/sub-agent-workspace.css")
    assert "grid-template-columns: minmax(0, 1fr);" in agents[agents.rindex(".sub-agent-workspace.is-docked {"):]
    assert "@container agent-dock (max-width: 520px)" in agents
    assert "container: agent-dock / inline-size;" in read("components/sub-agent-dock.css")
    assert "meta={counts.failed ?" in read("components/SubAgentWorkspace.tsx")


def test_the_readability_floor_covers_the_sub_agent_and_project_panels() -> None:
    scale = read("typography-scale.css")

    # These were 8.4-10.5px: the one dock the scale had never heard of. Badges keep --loom-font-badge.
    for selector in (".sub-agent-id", ".sub-agent-status", ".sub-agent-card-meta", ".sub-agent-toolbar-note", ".project-memory-tabs button"):
        assert selector in scale, selector
    assert ".sub-agent-filter button > span { font-size: var(--loom-font-badge) !important; }" in scale
    assert ".runtime-health { font-size: var(--loom-font-micro) !important; }" in scale


def test_the_fixture_has_an_embedded_browser_so_the_panel_can_be_opened() -> None:
    fixture = (ROOT / "desktop-react" / "scripts" / "fixtures" / "live-app.tsx").read_text(encoding="utf-8")

    # Without it BrowserWorkspace threw on `tabs.find` and the live-app fixture could not show the panel.
    assert "embeddedBrowser: async (command" in fixture
    assert "onEmbeddedBrowserChange" in fixture
