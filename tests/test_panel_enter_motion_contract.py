import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_right_docks_are_live_and_the_sidebar_snapshots_only_its_card_and_shared_controls() -> None:
    app = read("desktop-react/src/App.tsx")
    css = read("desktop-react/src/components/workspace-panels.css")

    assert 'import { flushSync } from "react-dom"' in app
    assert "runLayoutTransition" in app
    # Right-hand changes never wait for a snapshot: the docks run their own transitions on the real element (a second
    # click turns them around where they are painted) and the workspace follows by FLIP.
    assert 'const liveRightMotion = intent === "right-open" || intent === "right-close" || intent === "right-swap";' in app
    # A neutral change (a palette, a swap inside one dock) moves live as well: only the sidebar snapshots.
    assert 'const sidebarSnapshot = intent === "left-open" || intent === "left-close";' in app
    assert 'if (!sidebarSnapshot || typeof transitionDocument.startViewTransition !== "function")' in app
    # The sidebar's snapshot captures only the card and the two controls it shares with the header.
    names = set(re.findall(r"view-transition-name:\s*([\w-]+)", css))
    assert names == {"none", "loom-sidebar", "loom-sidebar-toggle", "loom-sidebar-compose"}, names
    assert "data-loom-layout-capture" not in css


def test_relocating_live_elements_use_flip_instead_of_disappear_reappear() -> None:
    app = read("desktop-react/src/App.tsx")
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "captureLayoutAnchors" in app
    assert "animateLayoutAnchors" in app
    # The sidebar's toggle and quill have two homes (the card's top row, the header); FLIP finds the one they are in.
    assert """selector: ':is(.sidebar-open > .sidebar, .sidebar-closed .thread-header-leading) [data-sidebar-control="toggle"]'""" in app
    assert 'selector: ".thread-header-copy"' in app
    assert 'selector: ".polished-thread-header-actions"' in app
    assert 'selector: ".transcript"' in app
    assert 'selector: ".composer"' in app
    assert 'selector: ".composer-hint"' in app
    assert 'selector: ".conversation-stage"' not in app
    assert 'selector: ".composer-stage"' not in app
    assert 'animation.id = "loom-layout-anchor"' in app
    assert "animation.commitStyles()" in app
    # Only the sidebar's shared toolbar controls move vertically (between the card's top row and the header).
    assert 'const deltaY = capture.kind === "header-leading" ? capture.topY - rect.top : 0;' in app

    assert "loom-readable" not in css
    assert "loom-transcript-old" not in css
    assert "loom-composer-old" not in css
    assert "loom-header-copy-old" not in css


def test_a61_depth_is_the_one_dock_language() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")
    docks = read("desktop-react/src/components/panel-language.css")

    # The sidebar's snapshot keeps the a61 keyframes on the panel clock.
    assert "animation: loom-panel-surface-in-left var(--loom-dur-panel) var(--loom-ease-move) both" in css
    assert "animation: loom-panel-surface-out-left var(--loom-dur-panel) var(--loom-ease-move) both" in css

    # The a61 character: a few pixels of travel and a shade of scale anchored at the dock's own edge, settling on the
    # out curve; leaving quickly (a61's snapshots left in 78-92 ms, the fast-out step is 120).
    assert "transform-origin: left center" in css and "transform-origin: right center" in css
    assert "transform: translate3d(-8px, 0, 0) scale(.992)" in css
    assert "transform: translate3d(8px, 0, 0) scale(.992)" in css
    assert "transform var(--loom-dur-panel) var(--loom-ease-out)" in css
    assert "transform var(--loom-dur-fast-out) var(--loom-ease-exit)" in css
    # Readable on the first frame: the fade starts a fifth of the way in, where the out curve is at .72.
    assert "opacity var(--loom-dur-fast) var(--loom-ease-out) calc(var(--loom-dur-fast) * -.2)" in css
    # The portal docks say the same through their presence.
    assert "transform: translate3d(8px, 0, 0) scale(.992)" in docks
    assert "opacity: .72;" in docks


def test_a_closed_dock_is_pinned_where_it_was_while_it_leaves() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    # Its track closes at once, so a leaving dock is taken out of the grid flow and pinned at its own edge (both grid
    # lines: an absolutely placed grid child with an auto end line reaches past the rail), over the conversation that
    # glides into its place, and hidden once its exit is over.
    assert ".workspace-panels.sidebar-closed > .sidebar,\n.workspace-panels.inspector-closed > .inspector {\n  position: absolute;" in css
    assert "grid-column: 1 / 2;" in css
    assert "grid-column: 3 / 4;" in css
    assert "visibility 0s linear var(--loom-dur-fast-out)" in css


def test_inspector_micro_motion_is_frozen_during_panel_handoff() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    # 7c34b9cb dropped the runtime-pane `animation: none` guard along with the
    # redundant runtime-pane-in entrance it existed to suppress, so only the
    # persistent micro-motion freeze is still part of the contract.
    assert ".runtime-orbit-dot" in css
    assert ".runtime-orbit-one::before" in css
    assert ".runtime-orbit-two::before" in css
    assert ".runtime-status-dot" in css
    assert "animation-play-state: paused !important" in css
    assert "scrollbar-gutter: stable" in css


def test_portal_docks_say_whether_they_are_open() -> None:
    review = read("desktop-react/src/components/ReviewWorkspace.tsx")
    agents = read("desktop-react/src/components/SubAgentDock.tsx")
    project = read("desktop-react/src/components/ProjectDetailsPanel.tsx")
    switching = read("desktop-react/src/components/panel-switching.css")

    assert 'data-open={open ? "true" : "false"}' in review
    assert 'data-open={open ? "true" : "false"}' in agents
    assert 'data-open={open ? "true" : "false"}' in project
    # A dock replaced by another goes at once rather than fading over the one that arrives.
    assert 'html:is([data-loom-panel-handoff="right-open"], [data-loom-panel-handoff="right-swap"])' in switching


def test_large_inspector_releases_closed_rows_and_skips_offscreen_paint_when_open() -> None:
    app = read("desktop-react/src/App.tsx")
    inspector = read("desktop-react/src/components/Inspector.tsx")
    inspector_css = read("desktop-react/src/components/Inspector.css")

    assert "items={inspectorPresence.mounted ? loom.items : EMPTY_TRANSCRIPT_ITEMS}" in app
    assert "memo(function Inspector" in inspector
    assert "memo(function RuntimeEvent" in inspector
    assert "onToggle={toggleEvent}" in inspector
    assert "scrollbar-gutter: stable" in inspector_css
    assert "content-visibility: auto" in inspector_css
    assert "contain-intrinsic-size: 51px" in inspector_css


def test_layout_flip_is_edge_anchored_and_installed_in_the_commit_frame() -> None:
    app = read("desktop-react/src/App.tsx")

    assert "anchorXForIntent" in app
    assert 'intent === "right-open" || intent === "right-close" || intent === "right-swap"' in app
    assert 'return { previous: capture.leftX, next: rect.left }' in app
    assert 'intent === "left-open" || intent === "left-close"' in app
    assert 'return { previous: capture.rightX, next: rect.right }' in app
    assert 'liveAnimations = animateLayoutAnchors(captures, intent)' in app
    assert 'committedInsideTransition = true' in app
    assert '{ translate: `${deltaX}px ${deltaY}px` }' in app
    assert 'willChange = "translate"' in app


def test_layout_flip_never_uses_width_changing_center_as_side_panel_anchor() -> None:
    app = read("desktop-react/src/App.tsx")

    assert "centerX: number" not in app
    assert "capture.centerX" not in app
    assert '{ transform: `translate3d(${deltaX}px,0,0)` }' not in app


def test_inspector_does_not_restart_content_animation_after_panel_handoff() -> None:
    inspector_css = read("desktop-react/src/components/Inspector.css")
    panel_css = read("desktop-react/src/components/workspace-panels.css")

    assert "runtime-pane-in" not in inspector_css
    assert ".runtime-pane" in inspector_css
    assert ".runtime-pane {\n  animation: none !important" not in panel_css
    assert "will-change: transform, opacity" in panel_css


def test_main_scrollbar_is_hidden_only_during_panel_motion() -> None:
    css = read("desktop-react/src/components/workspace-panels.css")

    assert "body.loom-panel-motion .workspace-panels .conversation-stage > .transcript-scroll" in css
    assert "scrollbar-color: transparent transparent !important" in css
    assert ".conversation-stage > .transcript-scroll::-webkit-scrollbar-thumb" in css
    assert "background: transparent !important" in css
