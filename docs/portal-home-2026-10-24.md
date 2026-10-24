# Loom home page, in the manner of darioamodei.com (2026-10-24)

The page at `/` of Loom Web (the editorial mode of `WebPortal.tsx`; `/classic` is untouched). Asked for as "look at his front
end, keep his font and style, design Loom a home page, minimal and premium". It is one column of type: no cards, no shadows,
no gradients, one colour of its own.

## What was read from the reference

Measured in the page itself (computed styles and its Webflow stylesheet), not guessed:

| | darioamodei.com | Loom home |
| --- | --- | --- |
| Typeface | Newsreader 24 Pt (400, 400 italic, 500, 600, 700) and Newsreader Display 60 pt (400, 600) | Newsreader, variable, optical-size axis (see below) |
| Page | ivory `#f0eee6`, text slate `#1f1e1d`; dark mode swaps them (`u-mode-invert`) | same two values, `--ed-paper` / `--ed-ink` |
| Secondary | cloud `#b0aea5` (dark), slate-light `#5e5d59` (light); rules `#3d3d3a` / `#d1cfc5` | same |
| Column | 620px with 20px side padding (text 580px) | `.editorial-shell` `max-width: 620px`, 20px padding |
| Body | 20px / 1.6, weight 400 | same |
| Headings | h3 24px / 1.2 / 600, `.7em` below; name 1.44em / 600 | `h2` 24px / 600; wordmark 28px / 600 |
| Links | always underlined, `.08em` thick, `.1em` off the text, colour moves in `.2s` linear | same; colour moves in `--loom-dur-fast` |
| Lists | "•" hanging, `.8em` between items | same |
| Control | a 40x20 pill, ivory track, dark 14px knob (right = dark mode), the only thing in the header | same pill, plus Sign in and the Loom icon (kept from earlier rounds) |
| Theme | follows the system until the reader chooses; the choice is kept | same (`loom.editorial.theme`) |
| Selection | `rgba(0,100,255,.26)` | same |
| Intro | a paragraph; "... More" opens the rest | the same disclosure, as a fold |

## What is Loom's own

- **The words** (`portalHomeCopy.ts`, both languages): the headline and the three words (Explore, Create, Stay in control)
  are the ones the home page already had; the rest says what Loom is, in his plain register.
- **A task, start to finish**: four log lines on a thread with a knot for each; the last knot is the accent colour, because
  the last line is waiting for you. It is the page's one picture, and it is set in type. The words fade in where they are
  (the motion language: what carries words does not move); only the line is drawn.
- **One accent**: the period of the wordmark and the waiting knot (steel blue, as before). The Loom icon at the far right of
  the header is the only other colour; it stays there on purpose (`portal-release.test.mjs` keeps it).
- **The page is shown when its typeface is in** (`data-type-ready`), with a 1.2s cap: words never swap fonts in front of the
  reader. Blocks arrive one after another (opacity only).

## Files

- `desktop-react/src/components/WebPortal.tsx`: the editorial markup and the state (theme, More, thread, type-ready).
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
have), `tsc --noEmit`, and the page itself at 390 / 858 / 1280px, both themes, both languages.
A browser pane that is not on screen draws no frames: transitions and `IntersectionObserver` do not run there, so finish
animations by script (`document.getAnimations().forEach((a) => a.finish())`) before reading colours, and set the thread's
`data-state` to `in` by hand.
