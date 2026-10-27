export { liquidVertexShader, liquidFragmentShader } from "./liquid-glass-reference/shaders";

// Glassify cursor settings from the reference site's Mouse component.
export const LIQUID_OPTICS = {
  strength: 0.95, pinch: 7.7, aberration: 0.12,
  reflection: 0.15, tint: 0.05, blur: 4,
} as const;

const uniformNames = [
  "u_texture", "u_resolution", "u_globalBounds", "u_roiBounds",
  "u_positions[0]", "u_lensSizes[0]", "u_tintColors[0]", "u_lensSettings[0]",
  "u_lensOptical[0]", "u_lensExtra[0]", "u_lensCount", "u_zoomAmount", "u_time",
  "u_blurSamples", "u_rimSamples", "u_enableZIndex", "u_mergeMode", "u_mergeSmoothness",
  "u_enableBackdropBlur", "u_enableReflections", "u_reflectionReach", "u_reflectionBlur",
  "u_reflectionSqueeze", "u_reflectionRayDir", "u_reflectionFade", "u_reflectionRimSoftness",
] as const;
type UniformName = typeof uniformNames[number];

// Host adapter for the reference's renderLoopCallback uniform contract.
// All optical equations live in the unmodified vendored shaders.
export function createReferenceLensRenderer(gl: WebGLRenderingContext, program: WebGLProgram) {
  const uniforms = Object.fromEntries(uniformNames.map(name => [name, gl.getUniformLocation(program, name)])) as Record<UniformName, WebGLUniformLocation | null>;
  const positions = new Float32Array(100);
  const sizes = new Float32Array(100);
  const tints = new Float32Array(200);
  const settings = new Float32Array(200);
  const opticals = new Float32Array(200);
  const extras = new Float32Array(200);
  for (let index = 0; index < 50; index++) extras[index * 4 + 2] = 1;

  return ({ roiWidth, roiHeight, centerX, centerY, width, height, screenDpr, time,
    strength = LIQUID_OPTICS.strength, aberration = LIQUID_OPTICS.aberration }: {
    roiWidth: number; roiHeight: number; centerX: number; centerY: number;
    width: number; height: number; screenDpr: number; time: number;
    strength?: number; aberration?: number;
  }) => {
    // ROI-local bottom-up UVs, physical lens sizes, and screenDpr, independent
    // of the texture's capture resolution: exactly the reference convention.
    positions[0] = centerX / roiWidth;
    positions[1] = 1 - centerY / roiHeight;
    sizes[0] = width * screenDpr; sizes[1] = height * screenDpr;
    tints.set([1, 1, 1, LIQUID_OPTICS.tint]);
    settings.set([0, strength, LIQUID_OPTICS.pinch, 9999 * screenDpr]);
    opticals.set([LIQUID_OPTICS.blur, LIQUID_OPTICS.reflection, 0, 0]);
    extras.set([0, aberration, 1, 0]);
    gl.useProgram(program);
    gl.uniform1i(uniforms.u_texture, 0);
    gl.uniform2f(uniforms.u_resolution, Math.max(1, Math.round(roiWidth * screenDpr)), Math.max(1, Math.round(roiHeight * screenDpr)));
    gl.uniform4f(uniforms.u_globalBounds, 0, 0, 1, 1);
    gl.uniform4f(uniforms.u_roiBounds, 0, 0, 1, 1);
    gl.uniform2fv(uniforms["u_positions[0]"], positions);
    gl.uniform2fv(uniforms["u_lensSizes[0]"], sizes);
    gl.uniform4fv(uniforms["u_tintColors[0]"], tints);
    gl.uniform4fv(uniforms["u_lensSettings[0]"], settings);
    gl.uniform4fv(uniforms["u_lensOptical[0]"], opticals);
    gl.uniform4fv(uniforms["u_lensExtra[0]"], extras);
    gl.uniform1i(uniforms.u_lensCount, 1);
    gl.uniform1f(uniforms.u_zoomAmount, 1);
    gl.uniform1f(uniforms.u_time, time);
    // Actual LiquidGlass instance settings from the reference homepage,
    // including merge mode and disabled reflections, not component defaults.
    gl.uniform1f(uniforms.u_blurSamples, 0);
    gl.uniform1f(uniforms.u_rimSamples, 0);
    gl.uniform1i(uniforms.u_enableZIndex, 0);
    gl.uniform1i(uniforms.u_mergeMode, 1);
    gl.uniform1f(uniforms.u_mergeSmoothness, 0.24);
    gl.uniform1i(uniforms.u_enableBackdropBlur, 0);
    gl.uniform1i(uniforms.u_enableReflections, 0);
    gl.uniform1f(uniforms.u_reflectionReach, 136 * screenDpr);
    gl.uniform1f(uniforms.u_reflectionBlur, screenDpr * 0.15);
    gl.uniform1f(uniforms.u_reflectionSqueeze, 100);
    gl.uniform1f(uniforms.u_reflectionRayDir, 1);
    gl.uniform1f(uniforms.u_reflectionFade, 0.5);
    gl.uniform1f(uniforms.u_reflectionRimSoftness, 0.29);
    gl.disable(gl.SCISSOR_TEST);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  };
}
