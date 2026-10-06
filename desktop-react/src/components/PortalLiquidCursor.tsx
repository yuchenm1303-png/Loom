import { useEffect, useRef } from "react";

const ROI_SIZE = 260;
const BASE_WIDTH = 80;
const BASE_HEIGHT = 54;
const FREE_OFFSET_Y = -32;
const SNAP_DISTANCE = 12;
const RELEASE_DISTANCE = 17;
const SNAP_PADDING = 10;
const WALLPAPER_URL = "https://smirel.com/download/wallpaper-beach-blue-v1-original.png";
const SNAP_SELECTOR = [
  "button:not(:disabled)",
  "a[href]",
  "input:not(:disabled)",
  "textarea:not(:disabled)",
  "select:not(:disabled)",
  "[role='button']",
  "[data-liquid-snap]",
].join(",");

type SpringValue = { value: number; velocity: number; target: number };

function stepSpring(spring: SpringValue, dt: number, stiffness: number, damping: number) {
  const acceleration = (spring.target - spring.value) * stiffness;
  spring.velocity += acceleration * dt;
  spring.velocity *= Math.exp(-damping * dt);
  spring.value += spring.velocity * dt;
}

function rectDistance(rect: DOMRect, x: number, y: number) {
  const dx = Math.max(rect.left - x, 0, x - rect.right);
  const dy = Math.max(rect.top - y, 0, y - rect.bottom);
  return Math.hypot(dx, dy);
}

function intersects(rect: DOMRect, left: number, top: number, size: number) {
  return rect.right >= left && rect.left <= left + size && rect.bottom >= top && rect.top <= top + size;
}

function cssNumber(value: string, fallback = 0) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function radiusFromStyle(style: CSSStyleDeclaration, rect: DOMRect) {
  const raw = style.borderTopLeftRadius || "0";
  if (raw.includes("%")) return Math.min(rect.width, rect.height) * cssNumber(raw) / 100;
  return Math.min(cssNumber(raw), Math.min(rect.width, rect.height) / 2);
}

function roundedRect(ctx: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number) {
  const r = Math.max(0, Math.min(radius, width / 2, height / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function visibleColor(value: string) {
  return value !== "transparent" && value !== "rgba(0, 0, 0, 0)" && value !== "rgba(0,0,0,0)";
}

function safeImageForCanvas(img: HTMLImageElement) {
  if (!img.complete || img.naturalWidth <= 0 || img.naturalHeight <= 0) return false;
  try {
    const url = new URL(img.currentSrc || img.src, window.location.href);
    return url.origin === window.location.origin || img.crossOrigin === "anonymous";
  } catch {
    return false;
  }
}

function drawImageFit(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  rect: DOMRect,
  style: CSSStyleDeclaration,
  roiLeft: number,
  roiTop: number,
) {
  const fit = style.objectFit || "fill";
  const boxX = rect.left - roiLeft;
  const boxY = rect.top - roiTop;
  let sx = 0;
  let sy = 0;
  let sw = img.naturalWidth;
  let sh = img.naturalHeight;

  if (fit === "cover" || fit === "contain") {
    const boxRatio = rect.width / Math.max(rect.height, 1);
    const imageRatio = img.naturalWidth / Math.max(img.naturalHeight, 1);
    const cropWidth = fit === "cover" ? imageRatio > boxRatio : imageRatio < boxRatio;
    if (cropWidth) {
      sw = img.naturalHeight * boxRatio;
      sx = (img.naturalWidth - sw) / 2;
    } else {
      sh = img.naturalWidth / boxRatio;
      sy = (img.naturalHeight - sh) / 2;
    }
  }

  try {
    ctx.drawImage(img, sx, sy, sw, sh, boxX, boxY, rect.width, rect.height);
  } catch {
    // Skip any image the browser refuses to expose to canvas.
  }
}

function drawWallpaper(
  ctx: CanvasRenderingContext2D,
  image: HTMLImageElement | null,
  roiLeft: number,
  roiTop: number,
  viewportWidth: number,
  viewportHeight: number,
) {
  ctx.fillStyle = "#21313c";
  ctx.fillRect(0, 0, ROI_SIZE, ROI_SIZE);
  if (image?.complete && image.naturalWidth > 0 && image.naturalHeight > 0) {
    const scale = Math.max(viewportWidth / image.naturalWidth, viewportHeight / image.naturalHeight);
    const drawnWidth = image.naturalWidth * scale;
    const drawnHeight = image.naturalHeight * scale;
    const pageX = (viewportWidth - drawnWidth) * 0.54;
    const pageY = (viewportHeight - drawnHeight) * 0.5;

    const srcX = Math.max(0, (roiLeft - pageX) / scale);
    const srcY = Math.max(0, (roiTop - pageY) / scale);
    const srcRight = Math.min(image.naturalWidth, (roiLeft + ROI_SIZE - pageX) / scale);
    const srcBottom = Math.min(image.naturalHeight, (roiTop + ROI_SIZE - pageY) / scale);
    const srcWidth = Math.max(0, srcRight - srcX);
    const srcHeight = Math.max(0, srcBottom - srcY);
    if (srcWidth > 0 && srcHeight > 0) {
      const destX = pageX + srcX * scale - roiLeft;
      const destY = pageY + srcY * scale - roiTop;
      ctx.drawImage(image, srcX, srcY, srcWidth, srcHeight, destX, destY, srcWidth * scale, srcHeight * scale);
    }
  }

  const veil = ctx.createLinearGradient(0, -roiTop, 0, viewportHeight - roiTop);
  veil.addColorStop(0, "rgba(4,10,16,.17)");
  veil.addColorStop(1, "rgba(4,10,16,.28)");
  ctx.fillStyle = veil;
  ctx.fillRect(0, 0, ROI_SIZE, ROI_SIZE);

  const cx = viewportWidth * 0.5 - roiLeft;
  const cy = viewportHeight * 0.34 - roiTop;
  const radius = Math.max(viewportWidth, viewportHeight) * 0.72;
  const vignette = ctx.createRadialGradient(cx, cy, radius * 0.24, cx, cy, radius);
  vignette.addColorStop(0, "rgba(3,8,14,0)");
  vignette.addColorStop(1, "rgba(3,8,14,.22)");
  ctx.fillStyle = vignette;
  ctx.fillRect(0, 0, ROI_SIZE, ROI_SIZE);
}

function drawTextNode(
  ctx: CanvasRenderingContext2D,
  node: Text,
  style: CSSStyleDeclaration,
  roiLeft: number,
  roiTop: number,
  inheritedOpacity: number,
) {
  const value = node.data;
  if (!value.trim()) return;
  const color = style.color;
  if (!visibleColor(color)) return;
  const matches = Array.from(value.matchAll(/\S+\s*/g)).slice(0, 80);
  if (!matches.length) return;

  ctx.save();
  ctx.globalAlpha = inheritedOpacity;
  ctx.fillStyle = color;
  ctx.font = `${style.fontStyle || "normal"} ${style.fontWeight || "400"} ${style.fontSize || "16px"} ${style.fontFamily || "sans-serif"}`;
  ctx.textBaseline = "alphabetic";
  const letterAware = ctx as CanvasRenderingContext2D & { letterSpacing?: string };
  if ("letterSpacing" in letterAware) letterAware.letterSpacing = style.letterSpacing;

  const transform = style.textTransform;
  for (const match of matches) {
    const index = match.index ?? 0;
    const end = Math.min(value.length, index + match[0].length);
    const range = document.createRange();
    try {
      range.setStart(node, index);
      range.setEnd(node, end);
      for (const rect of Array.from(range.getClientRects())) {
        if (!intersects(rect, roiLeft, roiTop, ROI_SIZE)) continue;
        let text = match[0].replace(/\s+$/g, "");
        if (!text) continue;
        if (transform === "uppercase") text = text.toUpperCase();
        if (transform === "lowercase") text = text.toLowerCase();
        if (transform === "capitalize") text = text.replace(/\b\w/g, (part) => part.toUpperCase());
        ctx.fillText(text, rect.left - roiLeft, rect.top - roiTop + rect.height * 0.8, Math.max(rect.width + 2, 1));
      }
    } catch {
      // React may update a text node between range reads.
    } finally {
      range.detach();
    }
  }
  ctx.restore();
}

function parseBackdropBlur(style: CSSStyleDeclaration) {
  const extended = style as CSSStyleDeclaration & { webkitBackdropFilter?: string };
  const raw = style.backdropFilter || extended.webkitBackdropFilter || "";
  const match = raw.match(/blur\(([\d.]+)px\)/i);
  return match ? Math.min(24, cssNumber(match[1])) : 0;
}

function rasterizePortal(
  root: HTMLElement,
  canvas: HTMLCanvasElement,
  scratch: HTMLCanvasElement,
  wallpaper: HTMLImageElement | null,
  roiLeft: number,
  roiTop: number,
  dpr: number,
) {
  const ctx = canvas.getContext("2d", { alpha: true });
  const scratchCtx = scratch.getContext("2d", { alpha: true });
  if (!ctx || !scratchCtx) return false;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawWallpaper(ctx, wallpaper, roiLeft, roiTop, window.innerWidth, window.innerHeight);

  const content = root.querySelector<HTMLElement>(".loom-portal-shell") ?? root;

  const renderElement = (el: HTMLElement, parentOpacity: number) => {
    if (el.dataset.loomLiquidCursor === "true" || el.closest("[data-loom-liquid-cursor='true']")) return;
    if (el.closest(".cosmos") || el.classList.contains("beach-wallpaper")) return;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return;
    const ownOpacity = Math.max(0, Math.min(1, cssNumber(style.opacity, 1)));
    const opacity = parentOpacity * ownOpacity;
    if (opacity <= 0.002) return;

    const rect = el.getBoundingClientRect();
    if (!intersects(rect, roiLeft, roiTop, ROI_SIZE)) return;

    const localX = rect.left - roiLeft;
    const localY = rect.top - roiTop;
    const radius = radiusFromStyle(style, rect);
    const blur = parseBackdropBlur(style);

    if (blur > 0 && (el.classList.contains("cards") || el.classList.contains("loom-host-onboarding"))) {
      scratchCtx.setTransform(1, 0, 0, 1, 0, 0);
      scratchCtx.clearRect(0, 0, scratch.width, scratch.height);
      scratchCtx.filter = `blur(${Math.max(1, blur * dpr)}px)`;
      scratchCtx.drawImage(canvas, 0, 0);
      scratchCtx.filter = "none";
      ctx.save();
      roundedRect(ctx, localX, localY, rect.width, rect.height, radius);
      ctx.clip();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.globalAlpha = opacity;
      ctx.drawImage(scratch, 0, 0);
      ctx.restore();
    }

    if (visibleColor(style.backgroundColor)) {
      ctx.save();
      ctx.globalAlpha = opacity;
      ctx.fillStyle = style.backgroundColor;
      roundedRect(ctx, localX, localY, rect.width, rect.height, radius);
      ctx.fill();
      ctx.restore();
    }

    const borderWidth = Math.max(
      cssNumber(style.borderTopWidth),
      cssNumber(style.borderRightWidth),
      cssNumber(style.borderBottomWidth),
      cssNumber(style.borderLeftWidth),
    );
    if (borderWidth > 0 && style.borderTopStyle !== "none" && visibleColor(style.borderTopColor)) {
      ctx.save();
      ctx.globalAlpha = opacity;
      ctx.strokeStyle = style.borderTopColor;
      ctx.lineWidth = borderWidth;
      roundedRect(
        ctx,
        localX + borderWidth / 2,
        localY + borderWidth / 2,
        Math.max(0, rect.width - borderWidth),
        Math.max(0, rect.height - borderWidth),
        Math.max(0, radius - borderWidth / 2),
      );
      ctx.stroke();
      ctx.restore();
    }

    if (el instanceof HTMLImageElement && safeImageForCanvas(el)) {
      ctx.save();
      ctx.globalAlpha = opacity;
      drawImageFit(ctx, el, rect, style, roiLeft, roiTop);
      ctx.restore();
    }

    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      const text = el.value || el.placeholder;
      if (text && visibleColor(style.color)) {
        ctx.save();
        ctx.globalAlpha = opacity;
        ctx.fillStyle = style.color;
        ctx.font = `${style.fontStyle || "normal"} ${style.fontWeight || "400"} ${style.fontSize || "16px"} ${style.fontFamily || "sans-serif"}`;
        ctx.textBaseline = "middle";
        ctx.fillText(text, localX + cssNumber(style.paddingLeft), localY + rect.height / 2, Math.max(1, rect.width - cssNumber(style.paddingLeft) - cssNumber(style.paddingRight)));
        ctx.restore();
      }
    }

    for (const child of Array.from(el.childNodes)) {
      if (child.nodeType === Node.TEXT_NODE) {
        drawTextNode(ctx, child as Text, style, roiLeft, roiTop, opacity);
      } else if (child instanceof HTMLElement) {
        renderElement(child, opacity);
      }
    }
  };

  renderElement(content, 1);
  return true;
}

function createShader(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    console.warn("Loom liquid cursor shader failed:", gl.getShaderInfoLog(shader));
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}


function createProgram(gl: WebGLRenderingContext) {
  const vertex = createShader(gl, gl.VERTEX_SHADER, `
    attribute vec2 a_position;
    attribute vec2 a_uv;
    varying vec2 v_uv;
    void main() {
      v_uv = a_uv;
      gl_Position = vec4(a_position, 0.0, 1.0);
    }
  `);
  const fragment = createShader(gl, gl.FRAGMENT_SHADER, `
    precision highp float;
    varying vec2 v_uv;
    uniform sampler2D u_texture;
    uniform vec2 u_resolution;
    uniform vec2 u_lensCenter;
    uniform vec2 u_lensSize;
    uniform float u_strength;
    uniform float u_pinch;
    uniform float u_aberration;
    uniform float u_zoom;
    uniform float u_wobble;
    uniform float u_time;

    float sdRoundBox(vec2 p, vec2 b, float r) {
      vec2 q = abs(p) - b + vec2(r);
      return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
    }

    vec2 warpPoint(vec2 p, float radius) {
      float angle = atan(p.y, p.x);
      float wave =
        sin(angle * 3.0 + u_time * 1.7) * 0.46 +
        cos(angle * 5.0 - u_time * 2.3) * 0.28 +
        sin(angle * 7.0 + u_time * 3.1) * 0.16;
      vec2 radial = normalize(p + vec2(0.0001));
      vec2 tangent = vec2(-radial.y, radial.x);
      return p - radial * wave * u_wobble * radius * 0.055
               + tangent * sin(angle * 2.0 + u_time * 1.25) * u_wobble * radius * 0.012;
    }

    void main() {
      vec2 screenUv = vec2(v_uv.x, 1.0 - v_uv.y);
      vec2 pixel = screenUv * u_resolution;
      vec2 halfSize = max(u_lensSize * 0.5, vec2(2.0));
      float radius = max(2.0, min(halfSize.x, halfSize.y));
      vec2 local = warpPoint(pixel - u_lensCenter, radius);
      float d = sdRoundBox(local, halfSize, radius);
      if (d > 1.5) {
        gl_FragColor = vec4(0.0);
        return;
      }

      float e = 1.0;
      float dx = sdRoundBox(local + vec2(e, 0.0), halfSize, radius) - sdRoundBox(local - vec2(e, 0.0), halfSize, radius);
      float dy = sdRoundBox(local + vec2(0.0, e), halfSize, radius) - sdRoundBox(local - vec2(0.0, e), halfSize, radius);
      vec2 normal = normalize(vec2(dx, dy) + vec2(0.00001));
      float distNorm = clamp(1.0 + d / radius, 0.0, 1.0);
      float effectivePinch = u_pinch * (radius / 100.0);
      float displacement = pow(distNorm, max(0.12, effectivePinch)) * u_strength * 40.0;
      vec2 sampleUv = screenUv - normal * (displacement / u_resolution);

      vec2 centerUv = u_lensCenter / u_resolution;
      sampleUv = (sampleUv - centerUv) / max(u_zoom, 1.0) + centerUv;
      sampleUv = clamp(sampleUv, vec2(0.001), vec2(0.999));

      vec2 chroma = normal * u_aberration * 0.02 * distNorm;
      vec3 color;
      color.r = texture2D(u_texture, clamp(sampleUv + chroma, 0.0, 1.0)).r;
      color.g = texture2D(u_texture, sampleUv).g;
      color.b = texture2D(u_texture, clamp(sampleUv - chroma, 0.0, 1.0)).b;

      float edge = smoothstep(0.62, 1.0, distNorm);
      vec3 reflected = texture2D(u_texture, clamp(sampleUv + normal * 0.012 * edge, 0.0, 1.0)).rgb;
      color = mix(color, reflected, edge * 0.10);
      color = mix(color, vec3(1.0), 0.035);

      vec2 lightDir = normalize(vec2(-0.62, -0.78));
      float directional = pow(max(dot(normal, lightDir), 0.0), 5.0);
      float rim = edge * (0.035 + directional * 0.22);
      color += vec3(0.78, 0.92, 1.0) * rim;

      float mask = 1.0 - smoothstep(-1.15, 0.9, d);
      float edgeGlass = smoothstep(0.78, 1.0, distNorm) * 0.08;
      gl_FragColor = vec4((color + vec3(edgeGlass)) * mask, mask);
    }
  `);
  if (!vertex || !fragment) return null;
  const program = gl.createProgram();
  if (!program) return null;
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    console.warn("Loom liquid cursor program failed:", gl.getProgramInfoLog(program));
    gl.deleteProgram(program);
    return null;
  }
  return program;
}

export function PortalLiquidCursor() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dotRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const dot = dotRef.current;
    const root = canvas?.closest(".loom-portal-page") as HTMLElement | null;
    if (!canvas || !dot || !root) return;

    const finePointer = window.matchMedia("(pointer: fine)").matches;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!finePointer || reducedMotion) return;

    const gl = canvas.getContext("webgl", {
      alpha: true,
      antialias: false,
      premultipliedAlpha: true,
      preserveDrawingBuffer: false,
    });
    if (!gl) return;
    const program = createProgram(gl);
    if (!program) return;

    const capture = document.createElement("canvas");
    const scratch = document.createElement("canvas");
    const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 1.75));
    const pixelSize = Math.round(ROI_SIZE * dpr);
    for (const target of [canvas, capture, scratch]) {
      target.width = pixelSize;
      target.height = pixelSize;
    }

    const position = gl.createBuffer();
    const uv = gl.createBuffer();
    const texture = gl.createTexture();
    if (!position || !uv || !texture) return;

    gl.bindBuffer(gl.ARRAY_BUFFER, position);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, -1,1, 1,-1, 1,1]), gl.STATIC_DRAW);
    const positionLocation = gl.getAttribLocation(program, "a_position");
    gl.enableVertexAttribArray(positionLocation);
    gl.vertexAttribPointer(positionLocation, 2, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, uv);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0,0, 1,0, 0,1, 0,1, 1,0, 1,1]), gl.STATIC_DRAW);
    const uvLocation = gl.getAttribLocation(program, "a_uv");
    gl.enableVertexAttribArray(uvLocation);
    gl.vertexAttribPointer(uvLocation, 2, gl.FLOAT, false, 0, 0);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

    const uniforms = {
      texture: gl.getUniformLocation(program, "u_texture"),
      resolution: gl.getUniformLocation(program, "u_resolution"),
      lensCenter: gl.getUniformLocation(program, "u_lensCenter"),
      lensSize: gl.getUniformLocation(program, "u_lensSize"),
      strength: gl.getUniformLocation(program, "u_strength"),
      pinch: gl.getUniformLocation(program, "u_pinch"),
      aberration: gl.getUniformLocation(program, "u_aberration"),
      zoom: gl.getUniformLocation(program, "u_zoom"),
      wobble: gl.getUniformLocation(program, "u_wobble"),
      time: gl.getUniformLocation(program, "u_time"),
    };

    const wallpaper = new Image();
    wallpaper.crossOrigin = "anonymous";
    wallpaper.decoding = "async";
    wallpaper.src = WALLPAPER_URL;

    let pointerX = window.innerWidth / 2;
    let pointerY = window.innerHeight / 2;
    let pointerInside = false;
    let pressed = false;
    let activeTarget: HTMLElement | null = null;
    let raf = 0;
    let lastTime = performance.now();
    let lastCapture = 0;
    let lastRoiLeft = Number.NaN;
    let lastRoiTop = Number.NaN;
    let rasterDirty = true;
    let textureReady = false;
    let snapDirty = true;
    let running = false;

    const x: SpringValue = { value: pointerX, velocity: 0, target: pointerX };
    const y: SpringValue = { value: pointerY + FREE_OFFSET_Y, velocity: 0, target: pointerY + FREE_OFFSET_Y };
    const width: SpringValue = { value: BASE_WIDTH, velocity: 0, target: BASE_WIDTH };
    const height: SpringValue = { value: BASE_HEIGHT, velocity: 0, target: BASE_HEIGHT };
    const snap: SpringValue = { value: 0, velocity: 0, target: 0 };

    const findSnapTarget = () => {
      if (activeTarget?.isConnected) {
        if (rectDistance(activeTarget.getBoundingClientRect(), pointerX, pointerY) <= RELEASE_DISTANCE) return activeTarget;
      }
      activeTarget = null;
      let closest = SNAP_DISTANCE + 0.001;
      for (const candidate of Array.from(root.querySelectorAll<HTMLElement>(SNAP_SELECTOR))) {
        if (candidate.dataset.loomLiquidCursor === "true") continue;
        const style = getComputedStyle(candidate);
        if (style.pointerEvents === "none" || style.visibility === "hidden" || style.display === "none") continue;
        const rect = candidate.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        const distance = rectDistance(rect, pointerX, pointerY);
        if (distance <= closest) {
          closest = distance;
          activeTarget = candidate;
        }
      }
      return activeTarget;
    };

    const updateTargets = () => {
      if (!snapDirty && !activeTarget) return;
      snapDirty = false;
      const target = findSnapTarget();
      if (target) {
        const rect = target.getBoundingClientRect();
        x.target = rect.left + rect.width / 2;
        y.target = rect.top + rect.height / 2;
        width.target = Math.min(ROI_SIZE - 20, Math.max(BASE_WIDTH, rect.width + SNAP_PADDING * 2));
        height.target = Math.min(ROI_SIZE - 20, Math.max(BASE_HEIGHT, rect.height + SNAP_PADDING * 2));
        snap.target = 1;
      } else {
        x.target = pointerX;
        y.target = pointerY + FREE_OFFSET_Y;
        width.target = BASE_WIDTH;
        height.target = BASE_HEIGHT;
        snap.target = 0;
      }
    };

    const uploadTexture = (roiLeft: number, roiTop: number, now: number) => {
      const moved = Math.abs(roiLeft - lastRoiLeft) >= 0.75 || Math.abs(roiTop - lastRoiTop) >= 0.75;
      if (!rasterDirty && !moved) return;
      if (now - lastCapture < 32) return;
      lastCapture = now;
      lastRoiLeft = roiLeft;
      lastRoiTop = roiTop;
      rasterDirty = false;
      if (!rasterizePortal(root, capture, scratch, wallpaper.complete ? wallpaper : null, roiLeft, roiTop, dpr)) return;
      try {
        gl.bindTexture(gl.TEXTURE_2D, texture);
        if (!textureReady) {
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, capture);
          textureReady = true;
        } else {
          gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, gl.RGBA, gl.UNSIGNED_BYTE, capture);
        }
      } catch (error) {
        console.warn("Loom liquid cursor texture upload failed:", error);
        textureReady = false;
      }
    };

    const ensureFrame = () => {
      if (running) return;
      running = true;
      lastTime = performance.now();
      raf = window.requestAnimationFrame(frame);
    };

    const frame = (now: number) => {
      const dt = Math.min(0.032, Math.max(0.001, (now - lastTime) / 1000));
      lastTime = now;
      updateTargets();

      stepSpring(x, dt, 500, 60);
      stepSpring(y, dt, 500, 60);
      stepSpring(width, dt, 280, 30);
      stepSpring(height, dt, 280, 30);
      stepSpring(snap, dt, 240, 28);

      const roiLeft = Math.max(0, Math.min(Math.max(0, window.innerWidth - ROI_SIZE), x.value - ROI_SIZE / 2));
      const roiTop = Math.max(0, Math.min(Math.max(0, window.innerHeight - ROI_SIZE), y.value - ROI_SIZE / 2));
      uploadTexture(roiLeft, roiTop, now);

      canvas.style.transform = `translate3d(${roiLeft}px, ${roiTop}px, 0)`;
      dot.style.transform = `translate3d(${pointerX - 1.75}px, ${pointerY - 1.75}px, 0)`;
      canvas.style.opacity = pointerInside && textureReady ? "1" : "0";
      dot.style.opacity = pointerInside ? (snap.value > 0.4 ? ".42" : ".86") : "0";

      if (textureReady) {
        const pressWeight = pressed ? 1 : 0;
        const strength = (0.95 + (1.4 - 0.95) * snap.value) * (1 - pressWeight) + 4.05 * pressWeight;
        const pinch = (7.7 + (7.0 - 7.7) * snap.value) * (1 - pressWeight) + 5.5 * pressWeight;
        const aberration = 0.12 + (0.18 - 0.12) * Math.max(snap.value, pressWeight);
        const zoom = (1 + (1.22 - 1) * snap.value) * (1 - pressWeight) + 1.1 * pressWeight;
        const wobble = (0.25 + 0.12 * snap.value) * (1 - pressWeight) + 0.65 * pressWeight;

        gl.viewport(0, 0, canvas.width, canvas.height);
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.useProgram(program);
        gl.bindTexture(gl.TEXTURE_2D, texture);
        gl.uniform1i(uniforms.texture, 0);
        gl.uniform2f(uniforms.resolution, canvas.width, canvas.height);
        gl.uniform2f(uniforms.lensCenter, (x.value - roiLeft) * dpr, (y.value - roiTop) * dpr);
        gl.uniform2f(uniforms.lensSize, width.value * dpr * (pressed ? 0.96 : 1), height.value * dpr * (pressed ? 0.92 : 1));
        gl.uniform1f(uniforms.strength, strength);
        gl.uniform1f(uniforms.pinch, pinch);
        gl.uniform1f(uniforms.aberration, aberration);
        gl.uniform1f(uniforms.zoom, zoom);
        gl.uniform1f(uniforms.wobble, wobble);
        gl.uniform1f(uniforms.time, now / 1000);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
      }

      const settled =
        Math.abs(x.target - x.value) < 0.08 &&
        Math.abs(y.target - y.value) < 0.08 &&
        Math.abs(width.target - width.value) < 0.08 &&
        Math.abs(height.target - height.value) < 0.08 &&
        Math.abs(snap.target - snap.value) < 0.002 &&
        Math.abs(x.velocity) < 0.08 &&
        Math.abs(y.velocity) < 0.08 &&
        Math.abs(width.velocity) < 0.08 &&
        Math.abs(height.velocity) < 0.08;

      if (!pointerInside && settled) {
        running = false;
        return;
      }
      raf = window.requestAnimationFrame(frame);
    };

    const wake = () => ensureFrame();
    const handlePointerMove = (event: PointerEvent) => {
      pointerX = event.clientX;
      pointerY = event.clientY;
      pointerInside = true;
      rasterDirty = true;
      snapDirty = true;
      wake();
    };
    const handlePointerDown = () => { pressed = true; rasterDirty = true; snapDirty = true; wake(); };
    const handlePointerUp = () => { pressed = false; rasterDirty = true; snapDirty = true; wake(); };
    const handlePointerLeave = () => {
      pointerInside = false;
      pressed = false;
      activeTarget = null;
      snap.target = 0;
      snapDirty = true;
      wake();
    };
    const handlePointerEnter = () => { pointerInside = true; rasterDirty = true; snapDirty = true; wake(); };
    const handleScroll = () => { rasterDirty = true; snapDirty = true; wake(); };
    const handleResize = () => { rasterDirty = true; snapDirty = true; wake(); };

    const observer = new MutationObserver(() => { rasterDirty = true; snapDirty = true; wake(); });
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    const resizeObserver = new ResizeObserver(() => { rasterDirty = true; snapDirty = true; wake(); });
    resizeObserver.observe(root);

    wallpaper.addEventListener("load", () => { rasterDirty = true; wake(); }, { once: true });
    document.fonts?.ready.then(() => { rasterDirty = true; wake(); }).catch(() => {});

    root.addEventListener("pointermove", handlePointerMove);
    root.addEventListener("pointerdown", handlePointerDown);
    root.addEventListener("pointerup", handlePointerUp);
    root.addEventListener("pointercancel", handlePointerUp);
    root.addEventListener("pointerleave", handlePointerLeave);
    root.addEventListener("pointerenter", handlePointerEnter);
    window.addEventListener("scroll", handleScroll, true);
    window.addEventListener("resize", handleResize);
    window.addEventListener("blur", handlePointerLeave);
    document.documentElement.classList.add("loom-liquid-cursor-active");

    ensureFrame();

    return () => {
      window.cancelAnimationFrame(raf);
      observer.disconnect();
      resizeObserver.disconnect();
      root.removeEventListener("pointermove", handlePointerMove);
      root.removeEventListener("pointerdown", handlePointerDown);
      root.removeEventListener("pointerup", handlePointerUp);
      root.removeEventListener("pointercancel", handlePointerUp);
      root.removeEventListener("pointerleave", handlePointerLeave);
      root.removeEventListener("pointerenter", handlePointerEnter);
      window.removeEventListener("scroll", handleScroll, true);
      window.removeEventListener("resize", handleResize);
      window.removeEventListener("blur", handlePointerLeave);
      document.documentElement.classList.remove("loom-liquid-cursor-active");
      gl.deleteTexture(texture);
      gl.deleteBuffer(position);
      gl.deleteBuffer(uv);
      gl.deleteProgram(program);
    };
  }, []);

  return (
    <>
      <canvas
        ref={canvasRef}
        className="loom-liquid-cursor-canvas"
        data-loom-liquid-cursor="true"
        aria-hidden="true"
      />
      <div
        ref={dotRef}
        className="loom-liquid-cursor-dot"
        data-loom-liquid-cursor="true"
        aria-hidden="true"
      />
    </>
  );
}