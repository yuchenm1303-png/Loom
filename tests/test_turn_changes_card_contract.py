"""Contracts for the changed-files card under a finished turn (TurnArtifactsPreview.tsx).

The card used to be six stylesheet layers over `.turn-artifacts*` that only agreed by accident: three rows of
chrome for one edited file, the file's name three times, a native tooltip over the header. It is one component
and one stylesheet now. These tests pin what a screenshot cannot show: who owns the look, what was deleted so
it cannot grow back, and the rules the stylesheet is written under. The behaviour is checked in a real browser
by desktop-react/scripts/tests/turn-changes-browser.mjs.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"
CARD_CSS = COMPONENTS / "turn-artifacts-preview.css"
CARD_TSX = COMPONENTS / "TurnArtifactsPreview.tsx"

# The slot (`.turn-artifacts-slot`) is the completion choreography's, and stays; everything else of the old
# name was the card, and the card is `.tc-*` now.
RETIRED = (
    "turn-artifacts", "rich-turn-artifacts", "review-summary-card", "review-summary-header", "review-summary-action",
    "turn-artifacts-header", "turn-artifacts-icon", "turn-artifacts-copy", "turn-artifacts-stats",
    "turn-artifacts-action", "turn-artifacts-files", "turn-artifacts-file-strip", "turn-artifacts-file-stats",
    "turn-artifacts-more", "turn-artifacts-grid", "turn-artifacts-inner", "turn-artifacts-preview-action",
)


def sources() -> list[Path]:
    return [
        path for path in SRC.rglob("*")
        if path.suffix in {".css", ".ts", ".tsx"} and "node_modules" not in path.parts
    ]


def strip_fallbacks(css: str) -> str:
    """The stylesheet with every `var(--token, fallback)` removed whole, so a colour that is only a fallback is not counted."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: list[str] = []
    index = 0
    while index < len(css):
        if css.startswith("var(", index):
            index += len("var(")
            depth = 1
            while index < len(css) and depth:
                depth += {"(": 1, ")": -1}.get(css[index], 0)
                index += 1
            out.append("var()")
            continue
        out.append(css[index])
        index += 1
    return "".join(out)


def test_the_card_has_one_owner_and_the_old_layers_are_gone() -> None:
    for path in sources():
        text = path.read_text(encoding="utf-8")
        for name in RETIRED:
            # `.review-summary` is the review pane's own header; only the exact retired names count.
            assert not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text), f"{path.relative_to(ROOT)} still mentions {name}"
        if path != CARD_CSS and path.suffix == ".css":
            assert not re.search(r"\.tc-[a-z]", text), f"{path.relative_to(ROOT)} styles the card, which belongs to turn-artifacts-preview.css"

    css = CARD_CSS.read_text(encoding="utf-8")
    assert css.count(".tc {") == 1
    # The slot that unfolds the card at completion is the one thing left of the old name (turn-flow.css).
    flow = (COMPONENTS / "turn-flow.css").read_text(encoding="utf-8")
    assert re.search(r"^\.turn-artifacts-slot \{", flow, flags=re.M)
    assert re.search(r"^\.turn-artifacts-slot-inner \{", flow, flags=re.M)
    # Nothing outside the slot says how wide the card is: it takes the conversation's width, like the composer.
    assert re.search(r"\.tc \{[^}]*\bwidth: 100%;", css, flags=re.S)


def test_the_stylesheet_is_written_from_tokens_in_both_themes() -> None:
    css = CARD_CSS.read_text(encoding="utf-8")
    bare = strip_fallbacks(css)

    # A colour is a token or it is not there: the light theme is the same rules over other tokens.
    assert 'data-loom-theme="light"' not in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", bare)
    assert not re.search(r"\brgba?\(", bare)
    for token in ("loom-workspace-panel", "loom-workspace-line", "loom-workspace-ink", "loom-workspace-accent",
                  "semantic-success-strong", "semantic-danger-strong"):
        assert f"var(--{token}" in css, token

    # The product's shared rules: no :has() (css-has-invalidation), the readable type floor
    # (typography-scale.css), and only the weights the warm-up knows (fontWarmup.ts).
    assert ":has(" not in css
    for size in re.findall(r"font-size:\s*([\d.]+)px", css):
        assert float(size) >= 11.5, size
    warmed = {int(weight) for weight in re.findall(r"\d{3}", re.search(r"UI_FONT_WEIGHTS[^=]*=\s*\[(.*?)\]", (SRC / "fontWarmup.ts").read_text(encoding="utf-8"), flags=re.S).group(1))}
    for weight in re.findall(r"font-weight:\s*(\d+)", css):
        assert int(weight) in warmed, weight


def test_it_answers_to_its_own_width_and_to_reduced_motion() -> None:
    css = CARD_CSS.read_text(encoding="utf-8")

    # The review pane opens beside the conversation (the card's own "Review"), so the card is measured, not the window.
    assert "container: tc / inline-size;" in css
    assert "@container tc (max-width: 639px)" in css
    assert "@container tc (max-width: 459px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ':root[data-loom-reduced-motion="true"]' in css


def test_every_fact_is_drawn_once() -> None:
    css = CARD_CSS.read_text(encoding="utf-8")
    tsx = CARD_TSX.read_text(encoding="utf-8")

    # The figures are the size of the change. A bar that repeats them (full for every created file), a tile that
    # repeats the verb and a glyph that repeats each name were three more ways of saying what the text says.
    for retired in ("tc-bar", "tc-mark", "tc-file-glyph", "DiffBar", "FILE_GLYPHS", "tc-bar-in"):
        assert retired not in css and retired not in tsx, retired
    # The two actions are words: neither wears an outline, and only the way on has a colour.
    action = re.search(r"\.tc-action \{(.*?)\}", css, flags=re.S).group(1)
    assert re.search(r"\bborder: 0;", action)
    assert "box-shadow" not in css
    assert re.search(r"\.tc-review \{\s*color: var\(--tc-accent\);", css)


def test_the_markup_keeps_one_native_button_per_action_and_nothing_nested() -> None:
    tsx = CARD_TSX.read_text(encoding="utf-8")

    # A header that is not a button inside a button: the stretched layer (css) is what makes all of it clickable.
    assert 'className="tc-open"' in tsx
    assert 'className="tc-file"' in tsx
    assert 'className="tc-action tc-preview"' in tsx
    assert 'className="tc-action tc-review"' in tsx
    assert 'loom:review-open' in tsx
    assert 'loom:artifact-preview-open' in tsx
    assert re.search(r"\.tc-open::after \{[^}]*position: absolute;[^}]*inset: 0;", CARD_CSS.read_text(encoding="utf-8"), flags=re.S)
    # No native tooltip over the header: the labels say what the buttons do.
    assert "title={copy.reviewTitle}" not in tsx
    # The preview stays the file the turn touched last; the biggest change comes first (turnChanges.ts).
    assert "b.lastChange - a.lastChange" in tsx
    assert "(b.additions + b.deletions) - (a.additions + a.deletions)" in (COMPONENTS / "turnChanges.ts").read_text(encoding="utf-8")
    # A long list folds behind one row, and what is folded cannot be reached by keyboard or screen reader.
    assert "VISIBLE_FILES" in tsx
    assert "inert={!expanded}" in tsx
    assert "aria-expanded={expanded}" in tsx
    assert "aria-controls={moreId}" in tsx
    # Click handlers stop the event, as the card always did: the transcript's own handlers are not for it.
    assert tsx.count("event.stopPropagation();") >= 4


def test_both_languages_speak_for_the_card_and_the_old_tooltip_copy_is_gone() -> None:
    copy = (COMPONENTS / "runtimeCopy.ts").read_text(encoding="utf-8")

    for key in ("changedFilesLead", "changedFilesSubject", "moreChangedFiles", "fewerChangedFiles"):
        # the interface plus Chinese plus English
        assert len(re.findall(rf"\b{key}\b", copy)) == 3, key
    assert "reviewTitle" not in copy
    # The title is a verb and an object now, so the whole sentence is no longer a string of its own.
    assert "changedFilesTitle" not in copy
    # What a turn did to a file says "Created" or "Deleted" when that is all it did.
    assert re.search(r"changedFilesLead: \(count, kind\) => \(kind === \"created\" \? \"已创建\" : kind === \"deleted\" \? \"已删除\"", copy)
    assert re.search(r"changedFilesLead: \(count, kind\) => \(kind === \"created\" \? \"Created\" : kind === \"deleted\" \? \"Deleted\"", copy)
    # The click handler that used to open a path from a `code[title]` in the card was unreachable (the card
    # stops the event) and named a class that no longer exists.
    assert ".turn-artifacts-files" not in (COMPONENTS / "ReviewInteractionBridge.tsx").read_text(encoding="utf-8")


def test_the_card_reads_the_runtimes_diff_through_the_one_splitter() -> None:
    card = CARD_TSX.read_text(encoding="utf-8")
    changes = (COMPONENTS / "turnChanges.ts").read_text(encoding="utf-8")
    splitter = (COMPONENTS / "diffFiles.ts").read_text(encoding="utf-8")
    activity = (COMPONENTS / "activityModel.ts").read_text(encoding="utf-8")

    # The runtime's turn diff is difflib's `---`/`+++` pairs and never a `diff --git` line. A card that splits on git
    # headers alone puts every file's lines under the first file's name and leaves the rest at zero (it did, for the
    # 315 of 563 recorded turn diffs that touched more than one file).
    tracker = (ROOT / "app" / "agent_runtime" / "diff_tracker.py").read_text(encoding="utf-8")
    assert "difflib.unified_diff" in tracker
    assert "diff --git" not in tracker
    # So there is one splitter, it knows both shapes, and the summary line and the card share it.
    assert "export function splitDiffByFile" in splitter
    assert "diff --git" in splitter
    assert re.search(r'line\.startsWith\("--- "\) && lines\[index \+ 1\]\?\.startsWith\("\+\+\+ "\)', splitter)
    assert 'import { splitDiffByFile } from "./diffFiles";' in changes
    assert 'import { splitDiffByFile } from "./diffFiles";' in activity
    assert "function splitDiffByFile" not in activity
    assert "diff --git a" not in changes
    assert "diff --git a" not in card
    assert 'import { collectFiles, type DiffFile } from "./turnChanges";' in card
    assert "function collectFiles" not in card
    # What is counted and named is pinned by a unit test of the pure logic.
    assert (ROOT / "desktop-react" / "scripts" / "tests" / "turn-changes.test.mjs").exists()


def test_the_live_fixture_can_show_the_card_in_the_real_transcript() -> None:
    fixture = (ROOT / "desktop-react" / "scripts" / "fixtures" / "live-app.tsx").read_text(encoding="utf-8")

    assert 'params.get("changes") !== "1"' in fixture
    assert "turn-change-" in fixture
    # The browser test reads the same fixture the card is judged in.
    assert (ROOT / "desktop-react" / "scripts" / "tests" / "turn-changes-browser.mjs").exists()
