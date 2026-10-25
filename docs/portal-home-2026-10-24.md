# Loom home page, in the manner of darioamodei.com (2026-10-24; a full page since 2026-10-25)

The page at `/` of Loom Web (the editorial mode of `WebPortal.tsx`; `/classic` is untouched). Asked for as "look at his front
end, keep his font and style, design Loom a home page, minimal and premium". Its type and colour are the reference's: no
shadows, no gradients, one colour of its own, and a card only where the page has its one picture.

The first version (10-24) was also the reference's *measure*, one 620px column. On a wide window that read as "a small
vertical strip" (the user, 10-25: "can it become a normal full-screen home page, or stay minimal and premium"), so the same hand
was laid on a full page: see "The wide layout" below. The table is what was read from the reference; the "Loom home" column
says what the page does with it today.

## What was read from the reference

Measured in the page itself (computed styles and its Webflow stylesheet), not guessed:

| | darioamodei.com | Loom home |
| --- | --- | --- |
| Typeface | Newsreader 24 Pt (400, 400 italic, 500, 600, 700) and Newsreader Display 60 pt (400, 600) | Newsreader, variable, optical-size axis (see below) |
| Page | ivory `#f0eee6`, text slate `#1f1e1d`; dark mode swaps them (`u-mode-invert`) | same two values, `--ed-paper` / `--ed-ink` |
| Secondary | cloud `#b0aea5` (dark), slate-light `#5e5d59` (light); rules `#3d3d3a` / `#d1cfc5` | same |
| Column | 620px with 20px side padding (text 580px) | a 1360 to 1560px frame on a 12-column grid; paragraphs keep the 580px measure |
| Body | 20px / 1.6, weight 400 | same |
| Headings | h3 24px / 1.2 / 600, `.7em` below; name 1.44em / 600 | `h2` 24px / 600; wordmark 28px / 600 |
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
  since 10-25 it sits in a ruled card beside the intro on the first screen. The words fade in where they are (the motion
  language: what carries words does not move); only the line is drawn.
- **One accent**: the period of the wordmark and the waiting knot (steel blue, as before). The Loom icon at the far right of
  the header is the only other colour; it stays there on purpose (`portal-release.test.mjs` keeps it).
- **The page is shown when its typeface is in** (`data-type-ready`), with a 1.2s cap: words never swap fonts in front of the
  reader. Blocks arrive one after another (opacity only).

## The wide layout (2026-10-25)

What changed is the *frame*, not the hand: the same face, the same two colours, the same one accent, the same copy (plus a
request line, a lead for "Getting started" and the nav labels, both languages).

- **Frame.** The shell spans the window. Its three parts (header, body, footer) take `padding-inline: var(--ed-edge)`, where
  `--ed-edge = max(gutter, (100% - --ed-wide) / 2)` and `--ed-wide = clamp(1360px, 60vw, 1560px)`; the gutter is
  `clamp(20px, 4.4vw, 64px)`. Everything inside sits on a 12-column grid (`repeat(12, minmax(0, 1fr))`, gap `clamp(16px, 2vw, 28px)`).
- **Header** (80px, sticky): the name (a link that scrolls to the top), a centred nav (What it does, Getting started, Download),
  and on the right Sign in, the pill, and the Loom icon, last as before. It takes a hairline only once the page has moved
  under it (`data-stuck`, set from the page's own scroll: the page, not the window, is the scroller).
- **First screen.** The headline across the width, `clamp(40px, min(8.6vw, 13.4vh), 140px)` (the optical-size axis gives the
  Display cut), two lines, the second italic. Under it, in the grid: columns 1 to 5 the intro with "More", the two buttons and
  the release line; columns 7 to 12 the card (a task, start to finish). The hero is `min(100dvh - 80px, 900px)` tall and laid
  out **from the top**: free space in a tall window is left below the words. (A first version spread it with
  `space-between`; opening More then moved the intro and the card up, by up to the free space, since the row grew into it.)
- **What Loom does.** A display heading, then three columns, each opened by a hairline, a number (`01`), the word at 28px and the
  sentence at 18px. **Getting started**: the heading, a lead and the two buttons on the left (columns 1 to 5), three numbered
  steps on the right (7 to 12).
- **Footer.** A full-width hairline, the maker on the left, Updates and Classic home on the right.
- **Narrower.** 1100px: the words take six columns. 960px: the card goes under the words, the sections stack, the hero is as
  tall as its words. 760px: the nav goes and the capabilities become one column. 520px: phone sizes.
- **Motion.** Still opacity only for anything with words. The first screen arrives in order as before (header, the two lines of
  the headline, intro, buttons, release, card; the card's rows start after it). Below it, every heading, item and step carries
  `data-reveal` and is shown (`data-seen`) the first time an `IntersectionObserver` sees a fifth of it; the hairline that opens
  an item is drawn left to right (`editorial-rule-draw`, the only other thing that moves besides the thread's line). Without an
  observer everything is shown, and with reduced motion nothing waits to be seen. A jump from the nav glides (CSS
  `scroll-behavior`, off for reduced motion) and lands 24px under the header (`scroll-padding-top`).
- **Glass cursor.** `PortalLiquidCursor` redraws the page into its lens from the DOM: text, plain fills, borders, images and
  plain SVG, not pseudo-elements. So the numbers are elements (`.editorial-index`), and every control the lens should snap to
  carries `data-liquid-snap` (brand, nav, Sign in, pill, icon, the primary button).

## Files

- `desktop-react/src/components/WebPortal.tsx`: the editorial markup and the state (theme, More, thread, type-ready, stuck, reveal).
- `desktop-react/src/components/portalHomeCopy.ts`: all the words.
- `desktop-react/src/components/portal-editorial.css`: the page. `portal-home-icon.css` (icon), `portal-auth-dialog.css`
  (the sign-in dialog, retuned to the same two colours) are as before.
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
At 1700x889 the first screen holds the headline, the words and the whole card; at 1366x768 the card ends 30px above the fold.
A browser pane that is not on screen draws no frames: transitions and `IntersectionObserver` do not run there, so finish
animations by script (`document.getAnimations().forEach((a) => a.finish())`) before reading colours, and set the thread's
`data-state` to `in` by hand.
