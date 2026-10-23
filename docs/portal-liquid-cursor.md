# Homepage liquid cursor

The React portal and `artifacts/loom-site` use the same `PortalLiquidCursor`.
Rebuild the standalone script with `node desktop-react/scripts/build-portal-cursor.mjs`.

## Reference

The Candid reference at <https://invigorated-illustrations-883631.framer.app/>
publishes Glassify, LiquidGlass_1, Snappy and MagneticHover_Prod modules in its
[bundle source map](https://framerusercontent.com/sites/71OP8x9NcfOXaqvd3cdaVM/jrVWzt4799Mu6PJLYdCt1xwbaM34Kg75R2-5dFX5Roo.DE1c7s7k.mjs.map).
Loom adapts its single-lens optical pipeline and free/pinned/weight motion
arrangement to the existing renderer. It does not depend on Framer or include
the reference's general-purpose cluster engine, page scroll controller,
global canvas prototype patches or editor property controls.

## Responsibilities

- `portalLiquidOptics.ts`: optical settings and WebGL shaders. Rounded-box
  normals feed refraction, RGB separation, white tint and squeezed rim
  reflection. Base controls retain the reference's basic lens settings: strength
  0.95, pinch 7.7, aberration 0.12, reflection 0.15, tint 0.05, no idle wobble
  or zoom. Displacement is bounded by the lens radius and profile derivative,
  RGB separation is at most 0.18 CSS pixels per channel and only affects the
  rim, and reflected content is sampled within 4 CSS pixels of the surface.
  None of those distances scale with the viewport or capture size.
- `portalLiquidMotion.ts`: stable substepped springs and snap-weight blending.
  The free follower continues tracking the mouse while a separate anchor
  follows the snapped element. Release blends back without jumping.
- `portalLiquidCapture.ts`: DOM sampling of text, images, SVG, input values,
  switch pseudo-elements, backgrounds and borders, respecting ancestor clips.
- `PortalLiquidCursor.tsx`: magnetic controls, input/click handling,
  ROI lifetime, texture uploads and lifecycle cleanup. Capture size is held
  through morph/release; a continuous displacement limiter prevents horizontal
  cutoff bands on shallow controls. Animation sleeps after springs settle.

The default free lens remains 80 × 54 CSS pixels. Entry distance is 12 pixels,
exit distance 17 pixels and target padding 10 pixels. `data-liquid-snap` and
`data-magnetic-hover` provide explicit opt-in outside existing portal controls.
Current `.loom-site` links/buttons/inputs are also recognized without changing
the homepage markup. Inputs snap but do not move magnetically.

Loom retains press/release compression, native keyboard interaction and trusted
primary-click forwarding from just outside a snapped control. System and app
reduced-motion changes tear down the overlay and restore the native cursor.

## Validation

- `node --test desktop-react/scripts/tests/liquid-cursor-motion.test.mjs`
- `node --test desktop-react/scripts/tests/portal-release.test.mjs`
- Run Vite, then `node desktop-react/scripts/tests/liquid-cursor-optics-browser.mjs`
  to render neutral stripe and gradient textures through the actual shader.
  This detects RGB bands and reversed sampling, across small/wide/round lenses,
  press strength, 1×/1.75× capture resolution and different ROI/viewport sizes.
- Run Vite, then `node desktop-react/scripts/tests/liquid-cursor-browser.mjs`.
  Set `LOOM_TEST_ORIGIN`, `LOOM_PLAYWRIGHT_MODULE`, and `LOOM_CHROMIUM_PATH` as
  needed. Browser checks exercise WebGL, snapping, clicks, input, nested scroll,
  theme capture, reduced motion, device scale and sleep/wake behavior.

DOM capture remains a reconstruction, not a browser screenshot: arbitrary CSS
filters, masks and stacking contexts are not fully reproduced.
