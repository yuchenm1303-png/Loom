"""The account dialog: sign-in, registration, the signed-in account and the profile editor in one card.

It used to be three layers of styles (the sign-in form, a signed-in block written over it, a profile editor written over
that), a segmented tab bar, an icon tile, nested cards, a purple gradient button with a glow, a "Check again" button that
drew unstyled, and inner class names (`.loom-account-form`, `.loom-account-switch`) that the web portal's sheets also
style - and those sheets are loaded in the desktop app too. Now AccountDialog.tsx draws one column centred on an
identity (an empty circle that becomes your picture), account-auth.css owns everything inside the card as `.acct-*`, a
change of view glides instead of jumping, and the avatar crop speaks the same language.
docs/account-dialog-2026-10-26.md says why. These tests read the sources as text, like the other source contracts;
desktop-react/scripts/tests/account-dialog-browser.mjs plays the flows in a browser.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "desktop-react" / "src"
COMPONENTS = SRC / "components"
DIALOG = COMPONENTS / "AccountDialog.tsx"
SHEET = COMPONENTS / "account-auth.css"
CROP = COMPONENTS / "AvatarCropDialog.tsx"
CROP_SHEET = COMPONENTS / "avatar-crop.css"
FIXTURE = ROOT / "desktop-react" / "scripts" / "fixtures" / "live-app.tsx"

# What the rest of the app finds the dialog by (tests, the window rail, the panel language).
HOOKS = {"loom-account-backdrop", "loom-account-dialog", "loom-account-close", "pl-scrim", "crossfade-in", "crossfade-out"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def stripped(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def block(css: str, selector: str) -> str:
    match = re.search(rf"(?:^|\n){re.escape(selector)}\s*\{{([^}}]*)\}}", css)
    assert match, selector
    return match.group(1)


def test_the_card_owns_one_namespace_and_borrows_nothing_from_the_portal() -> None:
    dialog = read(DIALOG)
    css = stripped(read(SHEET))
    # portal-base.css / portal-modules.css style these names globally, and WebAppGate loads them in the desktop app.
    for portal in ("loom-account-form", "loom-account-switch", "loom-auth-v2", "login-form", "loom-account-tabs"):
        assert portal not in dialog, portal
        assert portal not in css, portal
    names = set(re.findall(r"\.([a-zA-Z][\w-]*)", css))
    strays = {name for name in names if not name.startswith("acct-") and name not in HOOKS and not re.match(r"(is|tone|has)-", name)}
    assert not strays, strays
    # One owner: no other sheet reaches into the card.
    for path in SRC.rglob("*.css"):
        if path != SHEET and "node_modules" not in path.parts:
            assert ".acct-" not in read(path), path.name
    # The header's own account button lives in the right tool dock now; its rules went with it.
    assert "thread-account" not in css
    assert ":has(" not in css


def test_one_identity_becomes_the_person() -> None:
    dialog = read(DIALOG)
    css = stripped(read(SHEET))
    # Signed out the circle is empty; signed in (and in the editor) it is your picture or your initial.
    for face in ("acct-mark-face is-empty", "acct-mark-face is-picture", "acct-mark-face is-initial"):
        assert face in dialog, face
    assert '<Crossfade identity={markKey} className="acct-mark-faces">' in dialog
    # A picture that cannot be drawn falls back to the initial instead of a broken image.
    assert "onError={() => setBrokenAvatar(drawable)}" in dialog
    # The faces are opaque and stacked: the old one stays while the new one settles over it, so the circle never dims.
    assert "background: var(--acct-solid);" in block(css, ".acct-mark-face")
    assert "animation: none;" in block(css, ".acct-mark-faces > .crossfade-out")
    # Signed in is a green dot on the circle (amber while the service is out of reach), born on the pop spring.
    assert 'className={`acct-mark-dot is-${status}`}' in dialog
    assert "scale var(--loom-dur-slow) var(--loom-spring-pop)" in css


def test_a_change_of_view_glides_and_the_card_hangs_from_a_line() -> None:
    dialog = read(DIALOG)
    css = stripped(read(SHEET))
    # Centred, a card that changes height moves its header; hung from a line only its lower edge moves. Two classes:
    # panel-language.css centres every scrim and is loaded after this sheet.
    assert "align-items: start;" in block(css, ".loom-account-backdrop.pl-scrim")
    # The height glides on the flow step (Web Animations on the scale), from wherever the edge is painted.
    assert "function useHeightMorph(" in dialog
    assert "{ duration: DUR.flow, easing: EASE.move }" in dialog
    assert "new ResizeObserver(" in dialog
    assert "if (!node || from === null || reduce) return;" in dialog
    # The words fade through; the view that leaves takes no room.
    assert '<Crossfade block identity={view} className="acct-views">' in dialog
    assert "position: absolute;" in block(css, ".acct-stage > .acct-views > .crossfade-out")
    out = block(css, ".loom-account-dialog .crossfade-out")
    assert "var(--loom-dur-fast-out)" in out and "var(--loom-ease-exit)" in out
    assert "animation-delay: calc(var(--loom-stagger) * 2.5);" in block(css, ".loom-account-dialog .crossfade-in")
    # Closing keeps showing what was shown, and the fields are cleared only once the card is gone.
    assert "if (open) shownViewRef.current = liveView;" in dialog
    assert "if (presence.mounted) return;" in dialog


def test_notes_sit_on_the_label_line_and_folds_make_their_own_room() -> None:
    dialog = read(DIALOG)
    css = stripped(read(SHEET))
    # Typing never pushes the form: a field's note (incomplete, the password rule, match / mismatch, a count) is on
    # the line of its label.
    assert dialog.count('className="acct-label-row"') >= 3
    assert "Looks incomplete" in dialog and "Doesn't match" in dialog
    # What does appear (the confirmation field, an error, a notice, the browser wait) folds open on the base step.
    for fold in ("<Fold open={register}>", "<Fold open={Boolean(message)}>", "<Fold open={offline}", "<Fold open={oauthWaiting !== null}>"):
        assert fold in dialog, fold
    closed = block(css, ".acct-fold")
    assert "grid-template-rows: 0fr;" in closed
    assert "grid-template-rows var(--loom-dur-base) var(--loom-ease-move)" in closed
    # A folded field cannot be reached, and the focus trap skips it.
    assert "aria-hidden={!open} inert={!open}" in dialog
    assert 'element.tabIndex >= 0 && !element.closest("[inert]")' in dialog


def test_one_ink_action_and_words_for_the_rest() -> None:
    css = stripped(read(SHEET))
    crop = stripped(read(CROP_SHEET))
    primary = block(css, ".acct-button.is-primary")
    assert "background-color: var(--pl-ink);" in primary and "color: var(--pl-surface-raised);" in primary
    assert "box-shadow" not in primary and "gradient" not in primary
    crop_primary = block(crop, ".loom-avatar-crop-actions button.is-primary")
    assert "background-color: var(--pl-ink);" in crop_primary and "gradient" not in crop_primary
    # No violet glow, no gradient fills left anywhere in the two sheets (the crop's checkerboard is its only gradient).
    assert "linear-gradient(180deg" not in css and "linear-gradient(180deg" not in crop
    # Buttons that are words take no press wash (press.css): the wash would draw a box the link does not have.
    dialog = read(DIALOG)
    for tag in re.findall(r"<button[^>]*?className=\"acct-link[^>]*>", dialog, flags=re.S):
        assert 'data-press="none"' in tag, tag[:80]
    # Sizes on the ladder, no px below the readability floor.
    for sheet in (css, crop):
        for size in re.findall(r"font-size:\s*([\d.]+)px", sheet):
            assert float(size) >= 12, size


def test_the_crop_frames_the_circle_the_picture_is_shown_in() -> None:
    crop = read(CROP)
    css = stripped(read(CROP_SHEET))
    assert '<span className="loom-avatar-crop-window" aria-hidden="true" />' in crop
    window = block(css, ".loom-avatar-crop-window")
    assert "border-radius: 50%;" in window and "999px" in window
    # The rule-of-thirds grid, the icon tile and the tip line are gone; the stage says how to move it.
    for retired in ("loom-avatar-crop-grid", "loom-avatar-crop-mark", "loom-avatar-crop-hint"):
        assert retired not in crop, retired
    assert "aria-label={image" in crop and 'role={image ? "group" : undefined}' in crop


def test_the_fixture_plays_the_account_service() -> None:
    fixture = read(FIXTURE)
    # ?account=in|out|offline|offline-in|unconfigured|error, sign-in / registration / quick sign-in / profile / sign-out.
    for mode in ('"unconfigured"', '"offline-in"', '"error"', '"in"'):
        assert mode in fixture, mode
    for call in ("accountLogin:", "accountRegister:", "accountOAuthStart:", "onAccountOAuthResult:", "accountUpdateProfile:", "accountLogout:", "accountCapabilities:"):
        assert call in fixture, call
    # Without ?account the user is simply signed in, as every other fixture test expects.
    assert "accountMode === null" in fixture
