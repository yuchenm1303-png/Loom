# Homepage liquid cursor

The React portal and `artifacts/loom-site` use the same `PortalLiquidCursor`.
Rebuild the standalone script with `node desktop-react/scripts/build-portal-cursor.mjs`.

## Original refraction source

`desktop-react/src/components/liquid-glass-reference/shaders.ts` contains the
**verbatim vertex and fragment shader strings** from the reference site's
`LiquidGlass_1.js`, recovered from its published bundle source map. URLs and
SHA-256 hashes are recorded in the adjacent `provenance.json`.

The shader equations, array sizes, original UV conversion, SDF, normal
calculation, displacement, blur, RGB separation and reflection code are
unchanged. No Loom-specific GLSL limiter, colour-fringe formula or optical
fallback is substituted. The regression suite pins the original fragment hash
and checks that the standalone homepage contains exactly the same string.

`portalLiquidOptics.ts` is only a WebGL uniform adapter. It follows the
reference's `renderLoopCallback`: lens positions are bottom-up ROI-local UVs;
lens sizes and `u_resolution` use screen DPR, independent of capture resolution;
`u_globalBounds` and `u_roiBounds` are both `(0,0,1,1)` for the single ROI.
It passes the reference Mouse lens settings (pinch 7.7, aberration .12, white
tint .05, blur 4, no zoom or wobble) and the actual homepage LiquidGlass instance
settings (merge enabled, smoothness .24, backdrop blur and reflections
disabled). Component defaults alone are not the published site's configuration.

One uniform is scaled on the host side: the bend. The shader bends the sampled
map by `strength * 40` pixels at the rim, and its slope there is that bend
times `pinch / 100`. At the reference strength that slope is 2.9, so the map
folds over itself: neighbouring pixels sample in reverse order, the glyphs
under the lens repeat, and a control the lens is mounted on reads as a
flickering double of its own label. `lensBendStrength` scales the strength so
the bend stays a tenth under the `100 / pinch` pixels the map can carry
without folding. The vendored GLSL is not edited, and the shape of the
reference's bend curve is untouched: only its size is held to the lens.

## Loom integration

- `portalLiquidCapture.ts` reconstructs visible DOM content into a texture.
- `portalLiquidMotion.ts` handles free/pinned/weight spring motion.
- `PortalLiquidCursor.tsx` owns magnetic controls, click/input handling,
  capture region, uploads, lifecycle and idle sleep. Press feedback changes
  the lens geometry; it does not replace or alter the reference optics.

A capture window that moves is captured again in the same frame: the canvas is
drawn at the new position, so a texture taken at the old one would show the
page from somewhere else, and the control under the cursor would read as
flickering while the lens travels. The window also follows the target's place
on the page: a page that reflows under the cursor (a font that finishes
loading, a header that grows) moves the target, and a window locked to the old
place samples the page from somewhere else.

The WebGL canvas, the capture canvas and the scratch canvas are resized
together, never one alone. The texture is made of the capture canvas, so a
capture left at a different size is painted with the wrong proportions and cut
short, and the lens then shows the control under it as a smear.

The free capture region follows the reference's 260px locked box. It expands
when needed to contain a large snapped control and stays allocated through
snap/release to avoid invalidating the texture every frame. The cursor is
80 × 54 CSS pixels, with 12px snap entry, 17px release and 10px target padding.
System/app reduced motion tears down the overlay and restores the native cursor.

## Validation

- `node --test desktop-react/scripts/tests/liquid-cursor-motion.test.mjs`
- `node --test desktop-react/scripts/tests/portal-release.test.mjs`
- With Vite running, `node desktop-react/scripts/tests/liquid-cursor-optics-browser.mjs`
  checks original shader compilation, actual uniforms, ROI/DPR mapping and
  asymmetric texture orientation across small, wide and round lenses.
- `node desktop-react/scripts/tests/liquid-cursor-browser.mjs` checks motion,
  clicks, input, scroll, themes, reduced motion and idle sleep at 1×/2×.

Browser tests accept `LOOM_TEST_ORIGIN`, `LOOM_PLAYWRIGHT_MODULE` and
`LOOM_CHROMIUM_PATH`; set `LOOM_CURSOR_SCREENSHOT_DIR` to save visual evidence.
DOM capture remains a reconstruction, not a browser screenshot: arbitrary CSS
filters, masks and stacking contexts are not fully reproduced.
