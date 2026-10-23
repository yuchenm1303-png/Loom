// Single-lens adaptation of the Glassify / LiquidGlass optical pipeline.
// Reference and integration notes: docs/portal-liquid-cursor.md.
export const LIQUID_OPTICS = {
  strength: 0.95,
  pinch: 7.7,
  aberration: 0.12,
  reflection: 0.15,
  tint: 0.05,
} as const;

export const liquidVertexShader = `
  attribute vec2 a_position;
  attribute vec2 a_uv;
  varying vec2 v_uv;
  void main() {
    v_uv = a_uv;
    gl_Position = vec4(a_position, 0.0, 1.0);
  }
`;

export const liquidFragmentShader = `
  precision highp float;
  varying vec2 v_uv;
  uniform sampler2D u_texture;
  uniform vec2 u_resolution;
  uniform vec2 u_viewport;
  uniform vec2 u_lensCenter;
  uniform vec2 u_lensSize;
  uniform float u_strength;
  uniform float u_pinch;
  uniform float u_aberration;
  uniform float u_zoom;
  uniform float u_wobble;
  uniform float u_time;
  uniform float u_reflection;
  uniform float u_tint;
  uniform float u_dpr;

  float sdRoundBox(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + vec2(r);
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
  }

  vec2 warpPoint(vec2 p, float radius) {
    float angle = atan(p.y, p.x);
    float wave = sin(angle * 3.0 + u_time * 1.7) * 0.46
      + cos(angle * 5.0 - u_time * 2.3) * 0.28
      + sin(angle * 7.0 + u_time * 3.1) * 0.16;
    return p - normalize(p + vec2(0.0001)) * wave * u_wobble * radius * 0.055;
  }

  void main() {
    vec2 screenUv = vec2(v_uv.x, 1.0 - v_uv.y);
    // Optics use CSS pixels, independent of capture resolution and ROI size.
    vec2 pixel = screenUv * u_resolution / u_dpr;
    vec2 halfSize = max(u_lensSize * 0.5 / u_dpr, vec2(2.0));
    float radius = max(2.0, min(halfSize.x, halfSize.y));
    vec2 local = warpPoint(pixel - u_lensCenter / u_dpr, radius);
    float d = sdRoundBox(local, halfSize, radius);
    if (d > 1.5) { gl_FragColor = vec4(0.0); return; }

    // Analytic rounded-box normal, as in calculateLens. No finite-difference
    // seam through the middle of a shallow, wide snapped button.
    vec2 q = abs(local) - halfSize + vec2(radius);
    vec2 signP = vec2(local.x >= 0.0 ? 1.0 : -1.0, local.y >= 0.0 ? 1.0 : -1.0);
    vec2 normal;
    if (q.x > 0.0 && q.y > 0.0) normal = normalize(q) * signP;
    else if (q.x > q.y) normal = vec2(signP.x, 0.0);
    else normal = vec2(0.0, signP.y);
    float distNorm = clamp(1.0 + d / radius, 0.0, 1.0);
    float displacement = pow(distNorm, max(0.12, u_pinch * radius / 100.0)) * u_strength * 40.0;

    vec2 captureSize = u_resolution / u_dpr;
    vec2 boundary = min(pixel, captureSize - pixel);
    float edgeSafety = smoothstep(2.0, 30.0, min(boundary.x, boundary.y));
    vec2 refraction = normal * displacement * edgeSafety;
    // Preserve Loom's continuous limiter on narrow targets and viewport edges.
    vec2 bendReach = max(vec2(22.0, 13.0), halfSize * vec2(0.68, 0.66));
    vec2 bendRatio = refraction / bendReach;
    refraction *= inversesqrt(vec2(1.0) + bendRatio * bendRatio);
    vec2 sampleUv = screenUv - refraction / captureSize;
    vec2 centerUv = u_lensCenter / u_resolution;
    sampleUv = (sampleUv - centerUv) / max(u_zoom, 1.0) + centerUv;
    sampleUv = clamp(sampleUv, vec2(0.001), vec2(0.999));

    // RGB separation is measured against the viewport, so changing the ROI
    // during a snap cannot suddenly change the apparent colour fringe width.
    vec2 chroma = normal * u_aberration * 0.02 * distNorm * edgeSafety * u_viewport / captureSize;
    vec3 color;
    color.r = texture2D(u_texture, clamp(sampleUv + chroma, 0.0, 1.0)).r;
    color.g = texture2D(u_texture, sampleUv).g;
    color.b = texture2D(u_texture, clamp(sampleUv - chroma, 0.0, 1.0)).b;
    color = mix(color, vec3(1.0), u_tint);

    // LiquidGlass's squeezed rim reflection: reach, warp, soft visibility,
    // distance fade. Only the outer rim contributes, keeping text clear.
    float squeezeStart = 0.95;
    float rimWidth = 1.0 - squeezeStart;
    float progress = max(0.0, (distNorm - squeezeStart) / rimWidth);
    float reach = pow(progress, 1.5);
    vec2 ray = normal * vec2(-1.0, -1.0);
    vec2 reflectedUv = screenUv + ray * reach * (vec2(2.0) + pow(distNorm, 3.0) * 0.1 * u_viewport) / captureSize;
    float visibility = smoothstep(0.91, 0.975, distNorm);
    float reflectionWeight = u_reflection * visibility * clamp(1.0 - reach * 0.5, 0.0, 1.0) * edgeSafety;
    color += texture2D(u_texture, clamp(reflectedUv, 0.0, 1.0)).rgb * reflectionWeight;

    float mask = 1.0 - smoothstep(-1.15, 0.9, d);
    gl_FragColor = vec4(color * mask, mask);
  }
`;
