# Loom home page, in the manner of darioamodei.com (2026-10-24; a full page since 2026-10-25)

The page at `/` of Loom Web (the editorial mode of `WebPortal.tsx`; `/classic` is untouched). Asked for as "look at his front
end, keep his font and style, design Loom a home page, minimal and premium". Its type and colour are the reference's: no
shadows, no gradients, one colour of its own, and a card only where the page has its one picture.

The first version (10-24) was also the reference's *measure*, one 620px column. On a wide window that read as "a small
vertical strip" (the user, 10-25: "can it become a normal full-screen home page, or stay minimal and premium"), so the same hand
was laid on a full page (v2, 10-25): a 140px headline across the width and a boxed card. The user then found the headline
"too big" and asked to coordinate the whole screen and make it more minimal and premium (v3, the same day): see "The wide
layout" below. The table is what was read from the reference; the "Loom home" column says what the page does with it today.

## What was read from the reference

Measured in the page itself (computed styles and its Webflow stylesheet), not guessed:

| | darioamodei.com | Loom home |
| --- | --- | --- |
| Typeface | Newsreader 24 Pt (400, 400 italic, 500, 600, 700) and Newsreader Display 60 pt (400, 600) | Newsreader, variable, optical-size axis (see below) |
| Page | ivory `#f0eee6`, text slate `#1f1e1d`; dark mode swaps them (`u-mode-invert`) | same two values, `--ed-paper` / `--ed-ink` |
| Secondary | cloud `#b0aea5` (dark), slate-light `#5e5d59` (light); rules `#3d3d3a` / `#d1cfc5` | same |
| Column | 620px with 20px side padding (text 580px) | a 1280 to 1440px frame on a 12-column grid; paragraphs keep the 580px measure |
| Body | 20px / 1.6, weight 400 | same |
| Headings | h3 24px / 1.2 / 600, `.7em` below; name 1.44em / 600; page title 60px Display | headline up to 72px / 400, a section's heading 30 to 44px / 400, an item's title 24px / 500; wordmark 24px / 600 |
| Links | always underlined, `.08em` thick, `.1em` off the text, colour moves in `.2s` linear | same; colour moves in `--loom-dur-fast` |
| Lists | "•" hanging, `.8em` between items | same |
| Control | a 40x20 pill, ivory track, dark 14px knob (right = dark mode), the only thing in the header | same pill, plus a small nav, Sign in and the Loom icon (kept from earlier rounds, always last) |
| Theme | follows the system until the reader chooses; the choice is kept | same (`loom.editorial.theme`) |
| Selection | `rgba(0,100,255,.26)` | same |
| Intro | a paragraph; "... More" opens the rest | the same disclosure, as a fold |

## What is Loom's own

- **The words** (`portalHomeCopy.ts`, both languages): the headline and the three words (Explore, Create, Stay in control)
  are the ones the home page already had; the rest says what Loom is, in his plain register.
- **A task, start to finish**: the request in quotation marks, then four log lines on a thread with a knot for each; the last
  knot is the accent colour, because the last line is waiting for you. It is the page's one picture, it is set in type, and
  since 10-25 it stands beside the intro on the first screen, opened by one hairline like every block below it (v2 had it in a
  filled, bordered card; v3 took the box away). The words fade in where they are (the motion language: what carries words
  does not move); only the line is drawn.
- **One accent**: the period of the wordmark and the waiting knot (steel blue, as before). The Loom icon at the far right of
  the header is the only other colour; it stays there on purpose (`portal-release.test.mjs` keeps it).
- **The page is shown when its typeface is in** (`data-type-ready`), with a 1.2s cap: words never swap fonts in front of the
  reader. Blocks arrive one after another (opacity only).

## The wide layout (2026-10-25)

What changed is the *frame*, not the hand: the same face, the same two colours, the same one accent, the same copy (plus a
request line, a lead for "Getting started" and the nav labels, both languages).

- **Frame.** The shell spans the window. Its three parts (header, body, footer) take `padding-inline: var(--ed-edge)`, where
  `--ed-edge = max(gutter, (100% - --ed-wide) / 2)` and `--ed-wide = clamp(1280px, 58vw, 1440px)`; the gutter is
  `clamp(20px, 4.4vw, 64px)`. Everything inside sits on a 12-column grid (`repeat(12, minmax(0, 1fr))`, gap `clamp(16px, 2vw, 28px)`).
- **Type is one quiet scale.** Headline `--ed-title = clamp(40px, 4.4vw, 72px)`; a section's heading `clamp(30px, 3vw, 44px)`; an
  item's title 24px; paragraphs 20px (the intro) and 18px; meta 16px; labels 13px capitals. 72px is what the five columns of
  the words hold: the second line of the headline is then as wide as the paragraph under it, so the column reads as one block.
  (v2 set it across the width at up to 140px, 126px on the user's screen, and he found it too big. 64px was tried: the
  headline then ends well before the paragraph, and looks like a title put on top of it.) Buttons are 18px, the wordmark 24px.
- **Header** (72px, sticky): the name (a link that scrolls to the top), a centred nav (What it does, Getting started, Download),
  and on the right Sign in, the pill, and the Loom icon, last as before. It takes a hairline only once the page has moved
  under it (`data-stuck`, set from the page's own scroll: the page, not the window, is the scroller).
- **First screen.** One grid of two groups. Columns 1 to 5: the headline (two lines, the second italic; the optical-size axis
  gives the Display cut), the intro with "More", the two buttons, the release line. Columns 7 to 12: the picture, a task start
  to finish, **opened by one hairline that stands on the headline's cap line** (Newsreader's ascent is its cap height, so at
  line-height 1.08 the cap line is .04em, 2.9px at 72px, below the top of the line box: the picture's `margin-top`; measured
  with canvas font metrics). No card: every block of the page is opened
  by a hairline (the picture, the three items, the steps), so the page has one way of drawing a block. The group sits where a
  share of the window's height puts it (`padding-block: clamp(40px, 19vh, 200px)`, hero `min-height` `min(100dvh - 72px, 900px)`),
  and free space is left below it: with `space-between` or centring, opening More moved the intro and the picture, since the
  row grew into the free space (13px at 1700x889, more on a taller window). Top-aligned, nothing above More moves.
- **What Loom does.** A heading, then three columns, each opened by a hairline, a number (`01`), the word at 24px and the
  sentence at 18px (balanced, so no line ends on a lone word). **Getting started**: the heading, a lead and the two buttons on
  the left (columns 1 to 5), three numbered steps on the right (7 to 12).
- **Footer.** A full-width hairline, the maker on the left, Updates and Classic home on the right.
- **Narrower.** 1100px: the words take six columns. 960px: the picture goes under the words, the sections stack, the headline is
  `clamp(40px, 7.2vw, 60px)`, the hero is as tall as its words. 960px: the nav moves into the menu and the header
  keeps the language, the account and the icon (the centred nav had been running into them from 761 to about 850px);
  760px: the language follows into the menu and the capabilities become one column.
  520px: the headline is `clamp(40px, 11.4vw, 52px)`.
- **Motion.** Still opacity only for anything with words. The first screen arrives in order as before (header, the two lines of
  the headline, intro, buttons, release, card; the card's rows start after it). Below it, every heading, item and step carries
  `data-reveal` and is shown (`data-seen`) the first time an `IntersectionObserver` sees a fifth of it; the hairline that opens
  an item is drawn left to right (`editorial-rule-draw`, the only other thing that moves besides the thread's line). Without an
  observer everything is shown, and with reduced motion nothing waits to be seen. A jump from the nav glides (CSS
  `scroll-behavior`, off for reduced motion) and lands 24px under the header (`scroll-padding-top`).
- **Glass cursor.** `PortalLiquidCursor` redraws the page into its lens from the DOM: text, plain fills, borders, images and
  plain SVG, not pseudo-elements. So the numbers are elements (`.editorial-index`), and every control the lens should snap to
  carries `data-liquid-snap` (brand, nav, Sign in, pill, icon, the primary button).

## The page's chrome, completed (2026-10-26)

The page had a full account dialog from the first version (Google/GitHub, email and password, create account, email
verification, password reset), but the only way into it was a plain text "Sign in" in the header; on a narrow window the
nav disappeared with nothing in its place, and the page could not change its own language. The authentication logic is
untouched; this round only completes the chrome around it.

- **Header.** The Smirel mark and a rule introduce the name; language (EN / 中文, writing the shared
`loom.settings.language`), an account pill (Sign in, or an avatar and the signed-in name), the theme pill, and — under
760px — a menu button follow; the Loom icon stays last. Under 430px the house mark gives its room to the account and the
menu. The account pill opens the same drawer as before. Everything carrying words still only fades.
- **Narrow header.** Under 760px the three section links and the language switch move into a menu panel under the header.
It also carries a direct download and the account entry; it closes on Escape, on a selection, when the drawer opens, or
when the window returns to the desktop header. It is a disclosure (`aria-expanded` / `aria-controls`), not a second page.
- **Download.** `#loom-download` now sits between “What Loom does” and “Getting started”: the platform, the live version
and installer size, the installer, and the release notes. The header's Download link scrolls there instead of starting a
download from the nav; the hero, the section and “Getting started” still point straight at the installer.
- **Footer.** Download, Documentation (the repository README), GitHub, Updates, Classic home. The maker line is unchanged.
- **Tests.** `portal-home.test.mjs` now also holds the visible account entry, the language switch, the narrow menu, the
download section and the footer links (17 tests).

### Lens stops (2026-10-26)

The glass cursor is no longer limited to the controls that carry `data-liquid-snap`. Every native control inside the
portal is a stop by selector: `button`, input buttons, `a[href]`, `summary`, and the button/link/switch/tab/menuitem roles.
That covers the header, footer, narrow menu and the buttons that only mount when the account dialog opens. The older
class selectors stay for whole surfaces (the input shell, the brand), so an inner input does not become a second stop.

- **Reading stops.** The task label, the quoted request, each thread row, the three capability titles, the three step
titles and the three download rows carry `data-liquid-snap`. They are text, not controls: the lens magnifies them but they
do not move.
- **Crop.** A reading stop asks for a tighter lens with `data-liquid-snap-pad` (6px on the thread and download rows, 8px on
titles); controls keep the 10px padding. The value is clamped to 4–18px.
- **Motion.** Only the curated magnetic marks translate under the pointer. Every other control is a still stop, which keeps
a dialog full of small buttons from twitching.
- **Cost.** The stop list is cached in `snapTargets` and refreshed from the existing `MutationObserver`, not queried on every
pointermove. That is what keeps a freshly mounted auth dialog covered without the flicker of remeasuring every button.

### The lens reads what it holds (2026-10-27)

The glass cursor was mounting on too much and refracting too hard: a thread row became a band across the page, a one-line lens folded its own text into three copies, and the inside of the glass was a dark hole because the texture had no page colour under the words.

- **Mount points.** Every control is still a stop, but three inline links are not: "More" in the intro, "Release notes" in the release line and "Smirel" in the footer carry `data-liquid-snap="false"`. They are sentences first; snapping there made the lens jump while reading.
- **Sizes.** `portalLiquidFrame.ts` owns the numbers. A control is framed whole with 10px of padding, capped at 300 x 92; a reading stop is capped at 420 wide and at least 58 high, so a 20px line sits in the flat middle of the glass instead of under the rim. The free lens is 84 x 58, 36px above the pointer.
- **Position.** A reading stop is centred on its painted words (`Range.getClientRects`), not on its box: the thread row is 820px wide but its sentence is 430px, and centring on the box parked the lens over empty paper.
- **Optics.** The reference shader spends `strength * 40` pixels of displacement at the rim. One figure for every lens turned a 46px-high row into three stacked copies of its line, so strength is now per kind: 0.72 free, 0.44 control, 0.22 reading, with less chromatic aberration when reading.
- **Capture.** `rasterizePortal` fills the texture with the resolved page colour (walking from `.loom-portal-page` to `html`, then `--ed-paper`) before drawing anything, and the wallpaper veil is no longer laid over a flat page. The shader writes an opaque interior, so an unfilled texture was painted black.

## The header on one row (2026-10-28)

- **One control row.** The nav links, the language switch, the account pill, the theme switch and the icon are all 36px
tall and centred on the header's one line, so their boxes share the same bottom edge (53.5px at the 72px header).
Before, the nav links were 44px, the icon 38px and the switch 20px, each centred on its own box. The nav links no longer
carry 14px of vertical padding; the language switch is a 36px, 44px-minimum button like the account pill, and the icon
is 36px with a 10px radius.
- **One type size.** Nav, language and account are 16px (the language and the account were 14px), the wordmark stays
24px; the meta gap is 12px and the nav gap tops out at 40px.
- **One hover.** Nav links and the language switch both take the ink colour and their underline on hover; the account
keeps its hairline and soft paper, the icon its 7% wash.
- **The narrow header starts at 960px.** At 761 to about 850px the centred nav ran into the account pill (34.7px at
761), and at 901px a squeezed language switch wrapped onto two lines; the nav now enters the menu at 960px (the first
width that holds the name, the centred nav and all five controls) and the resize handler closes the menu at 961px.
The language stays in the header until 760px, where it also enters the menu.
- **The account is a name, not a chip (second pass).** The bordered pill and its filled cream disc read as a chat-app badge
  beside an editorial header, so the account is plain text like the links around it: a 22px hairline ring holding the
  initial, the name at 16px, no border, no background, the name underlined on hover. It is still a 36px button on the same
  row, still opens the account dialog, and keeps its lens and magnetic marks.
- **The lockup shares one bottom line.** The wordmark carries transparent padding, so its ink was measured, not eyeballed:
  at 104×34 the ink ends 30/34 down the box, and `translateY(-7.5px)` (was −9px) puts that ink at 42px, half a pixel under
  the foot of "Loom." (41.5px). The divider was 22px tall and hung 5px below the text; it is now the 18px cap band of the
  name, its foot on the same 40.5px baseline. Checked at 1440 / 961 / 520px in light and dark.
- **Checked.** The built bundle served locally, at 390 / 761 / 850 / 960 / 961 / 1016 / 1440px, light and dark:
all five controls have the same 36px box and one baseline, no horizontal overflow, the menu opens and closes at 850 and
960, the theme switch flips, the account dialog opens and closes.

## Files

- `desktop-react/src/components/WebPortal.tsx`: the editorial markup and the state (theme, More, thread, type-ready, stuck, reveal, language, narrow menu, account entry).
- `desktop-react/src/components/portalHomeCopy.ts`: all the words, including the download block and the footer links.
- `desktop-react/src/components/portal-editorial.css`: the page. `portal-home-icon.css` (icon), `portal-auth-dialog.css`
  (the sign-in dialog, retuned to the same two colours) are as before.
- `desktop-react/src/components/PortalLiquidCursor.tsx`: the lens, its native-control selector, cached stops and the reading
  stops' crop.
- `desktop-react/src/assets/fonts/newsreader/`: the two font files, `OFL.txt`, and `README.md` (provenance, hashes, exact
  subsetting commands). Bundled through the build, so the gateway serves them hashed and immutable.
- `desktop-react/scripts/tests/portal-home.test.mjs`: the source contract of all of the above.

## Decisions worth knowing

- **Optical size**: Newsreader's two cuts (24 pt for text, 60 pt Display) are one variable font. `font-optical-sizing: auto`
  gives the text cut at 20px and the Display cut at 60px, as on the reference. The files are cut down to ASCII plus typographic
  punctuation and weights 400 to 650 (57 KB + 64 KB; the originals are 132 KB + 147 KB).
- **Chinese**: Newsreader has no Chinese; the stack goes Georgia for Latin, then Noto Serif SC, Source Han Serif SC, Songti SC,
  STSong, SimSun. Where none is installed (most Windows machines) SimSun is what is drawn. No Chinese web font is shipped.
- **Default theme** is the system's (it used to be dark); a stored choice wins.
- **Colours transition on elements** (one clock, `--loom-dur-fast`), not on registered custom properties: the two stack, and
  a link caught mid-switch stays half grey.

## Left alone

- `EditorialHome.tsx` / `editorial-home.css` / `public/newsreader-latin.woff2` (untracked, from 2026-10-22, imported by nothing)
  and the uncommitted product-introduction version of `WebPortal.tsx` (2026-10-23, imported a `portal-introduction.css` that
  was never written, so the web build failed). This page replaces the second; the first is dead code. The last copy of that
  WIP is in `C:/tmp/loom-home/backup/` on the machine where this was done.
- The sign-in dialog's own contract reds (`loom-dialog-enter` moves text and uses a literal `.19s`, a 19px size) are older than
  this page and not part of it.

## Checking

`node --test scripts/tests/portal-home.test.mjs scripts/tests/portal-release.test.mjs` (in `desktop-react`), the two scale
contracts (`tests/test_design_scale_contract.py`, `tests/test_motion_scale_contract.py`; they lose the entries this page used to
have), `tsc --noEmit`, and the page itself at 390 / 820 / 1024 / 1366 / 1700 / 1920 / 2560px, both themes, both languages
(Playwright with the installed Chrome gives real pixels; the Browser pane shows an emulated 1700px window as a thumbnail).
At 1700x940 (the user's window) the first screen holds the whole group with about 180px above it and 225px below; at 1366x768
the group ends 118px above the fold. Real pixels (Playwright with the installed Chrome) were read at 1700x940 in both themes
and in Chinese; the three card variants (boxed, hairline box, no box) and the headline at 56, 64 and 72px were compared by
injecting CSS into the same page.
A browser pane that is not on screen draws no frames: transitions and `IntersectionObserver` do not run there, so finish
animations by script (`document.getAnimations().forEach((a) => a.finish())`) before reading colours, and set the thread's
`data-state` to `in` by hand.
