# Verbatim LiquidGlass shaders

These are the `vertexShaderSource` and `fragmentShaderSource` string values
from `LiquidGlass_1.js` in the public source map of
<https://invigorated-illustrations-883631.framer.app/>.

`provenance.json` pins the module URL, source-map URL, complete module hash and
both shader hashes. GLSL inside the strings is unmodified. Keep host-specific
uniform binding in `../portalLiquidOptics.ts`; do not edit these shader bodies
to implement alternative refraction. `portal-release.test.mjs` verifies the
original fragment hash and production standalone shader parity.
