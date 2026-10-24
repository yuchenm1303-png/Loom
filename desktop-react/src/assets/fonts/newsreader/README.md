# Newsreader (home page typeface)

The Loom home page is set in **Newsreader**, the typeface darioamodei.com uses (its "Newsreader 24 Pt" text cut and
"Newsreader Display" 60 pt cut). Newsreader is by Production Type and is licensed under the SIL Open Font License 1.1
(`OFL.txt`, from the same package).

Both cuts are one variable font with an optical-size axis (`opsz`), so `font-optical-sizing: auto` picks the 24 pt-like
cut for text and the Display-like cut for large headings from the size alone.

## Where the files come from

Source: npm package `@fontsource-variable/newsreader@5.3.0`, files `newsreader-latin-opsz-normal.woff2` and
`newsreader-latin-opsz-italic.woff2` (the Google Fonts `latin` subset, 132 000 and 146 872 bytes).

| original | sha256 |
| --- | --- |
| `newsreader-latin-opsz-normal.woff2` | `6e4f2958c3a7c4a80acde4e5a679abe7e01bc1e30b92be3c7a8b696ef401d101` |
| `newsreader-latin-opsz-italic.woff2` | `5dfcd10d24af8c82927ba57f7983dd997f7b200594c09c35a1b1ed9fc4597506` |

## What was changed (fontTools 4.67.0, brotli 1.2.1)

The home page asks for weights 400 to 650 and sizes from 15px up, and its copy is ASCII with typographic punctuation, so
the files here carry only that: 57 KB + 64 KB instead of 132 KB + 147 KB.

```
python -m fontTools.subset <original> --unicodes=U+0020-007E,U+00A0,U+00B7,U+00D7,U+2010-2015,U+2018-201F,U+2022,U+2026,U+2191,U+2193,U+2212 --flavor=woff2 --layout-features='*' --no-hinting --notdef-outline --output-file=<subset>
python -m fontTools.varLib.instancer <subset> wght=400:650 opsz=14:72 --output=<file here>
```

(The instancer ran on a Latin-1 subset first; the order does not change the result.) Drawn side by side with the originals
on a canvas, 216 combinations of style, weight (400/500/600/650) and size (15 to 72px) differ by under 2% of the ink, apart
from three digits and symbols at 16px that differ by 5 to 7%. Characters outside the set (accented letters) fall to the next
face in the stack.

If the page ever needs more (another weight, accented Latin), start again from the original package files.
