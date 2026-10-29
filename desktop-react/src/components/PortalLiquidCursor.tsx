import { useEffect, useRef } from "react";
import { readingMount, paddedMount, mountOwner } from "./portalLiquidMount";
import { rasterizePortal } from "./portalLiquidCapture";
import { blendAnchor, constrainAdjacentMagneticOffsets, magnetAmplitude, stepSpring, type SpringValue } from "./portalLiquidMotion";
import { createReferenceLensRenderer, liquidVertexShader, liquidFragmentShader } from "./portalLiquidOptics";
import { useReducedMotion } from "../motion/useReducedMotion";

const FREE_ROI_SIZE = 260;
const BASE_WIDTH = 80;
const BASE_HEIGHT = 54;
const FREE_OFFSET_Y = -32;
const SNAP_DISTANCE = 12;
const RELEASE_DISTANCE = 17;
const FREE_ROI_PADDING = 64;
const ROI_DEADZONE = 35;
// The lens samples the page it is drawn on. A separate wallpaper would show
// through the refraction as a second, unrelated background.
const WALLPAPER_URL = "";
const CLICKABLE_SELECTOR = [
  "button", "a[href]", "input:not([type='hidden'])", "textarea", "select",
  "summary", "label", "[role='button']", "[role='link']", "[role='switch']",
  "[role='checkbox']", "[role='radio']", "[role='tab']", "[role='menuitem']",
  "[onclick]", "[tabindex]:not([tabindex='-1'])",
].join(",");
const SNAP_SELECTOR = [
  CLICKABLE_SELECTOR,
  ".loom-portal-brand",
  ".loom-primary-action:not(:disabled)",
  ".loom-secondary-action",
  ".loom-download-version a",
  ".loom-oauth-button:not(:disabled)",
  ".loom-input-shell",
  ".loom-code-input:not(:disabled)",
  ".loom-inline-link:not(:disabled)",
  ".loom-form-submit:not(:disabled)",
  ".loom-account-switch:not(:disabled)",
  ".loom-host-setup-retry:not(:disabled)",
  ".loom-profile-home-action:not(:disabled)",
  ".loom-site a[href], .loom-site button:not(:disabled), .loom-site input:not(:disabled)",
  "[data-liquid-snap='true']",
].join(",");

const MAGNETIC_SELECTOR = SNAP_SELECTOR;

// A control that moves with the magnet must not fight the pointer: an element can opt out
// with data-magnetic-hover="false" (and nothing else can).
const MAGNETIC_OPT_OUT = "[data-magnetic-hover='false']";

function rectDistance(rect: DOMRect, x: number, y: number) {
  const dx = Math.max(rect.left - x, 0, x - rect.right);
  const dy = Math.max(rect.top - y, 0, y - rect.bottom);
  return Math.hypot(dx, dy);
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
  gl.getExtension("OES_standard_derivatives");
  const vertex = createShader(gl, gl.VERTEX_SHADER, liquidVertexShader);
  const fragment = createShader(gl, gl.FRAGMENT_SHADER, liquidFragmentShader);
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
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    const canvas = canvasRef.current;
    const dot = dotRef.current;
    const root = (canvas?.closest(".loom-portal-page") ?? document.querySelector(".loom-portal-page")) as HTMLElement | null;
    if (!canvas || !dot || !root) return;

    const finePointer = window.matchMedia("(pointer: fine)").matches;
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
    let roiWidth = FREE_ROI_SIZE;
    let roiHeight = FREE_ROI_SIZE;

    const resizeSurfaces = (cssWidth: number, cssHeight: number) => {
      const nextWidth = Math.max(1, Math.round(cssWidth * dpr));
      const nextHeight = Math.max(1, Math.round(cssHeight * dpr));
      // Every surface has to match. The capture and scratch canvases are what
      // the texture is made of, and a capture left at its default size is
      // painted with the wrong proportions and cut short: the lens then shows
      // the control under it as a smear.
      if (canvas.width === nextWidth && canvas.height === nextHeight &&
          capture.width === nextWidth && capture.height === nextHeight &&
          scratch.width === nextWidth && scratch.height === nextHeight) return false;
      for (const target of [canvas, capture, scratch]) {
        target.width = nextWidth;
        target.height = nextHeight;
      }
      canvas.style.width = `${cssWidth}px`;
      canvas.style.height = `${cssHeight}px`;
      return true;
    };

    resizeSurfaces(roiWidth, roiHeight);

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
    const uvLocation = gl.getAttribLocation(program, "a_texCoord");
    gl.enableVertexAttribArray(uvLocation);
    gl.vertexAttribPointer(uvLocation, 2, gl.FLOAT, false, 0, 0);

    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

    const drawReferenceLens = createReferenceLensRenderer(gl, program);

    const wallpaper = new Image();
    wallpaper.crossOrigin = "anonymous";
    wallpaper.decoding = "async";
    if (WALLPAPER_URL) wallpaper.src = WALLPAPER_URL;

    let pointerX = window.innerWidth / 2;
    let pointerY = window.innerHeight / 2;
    let pointerInside = false;
    let pressed = false;
    const pressure: SpringValue = { value: 0, velocity: 0, target: 0 };
    let activeTarget: HTMLElement | null = null;
    let raf = 0;
    let lastTime = performance.now();
    let lastCapture = 0;
    let lastRoiLeft = Number.NaN;
    let lastRoiTop = Number.NaN;
    let roiLeft = Number.NaN;
    let roiTop = Number.NaN;
    let roiLockedTarget: HTMLElement | null = null;
    let roiLockedBaseX = Number.NaN;
    let roiLockedBaseY = Number.NaN;
    let snappedRoiWidth = FREE_ROI_SIZE;
    let snappedRoiHeight = FREE_ROI_SIZE;
    let rasterDirty = true;
    let caretVisible = true;
    // CSS ::after transitions do not mutate DOM attributes on every frame.
    // Keep sampling while the snapped control's visual thumb is animating.
    let refractionMotionUntil = 0;
    let textureReady = false;
    let snapDirty = true;
    let running = false;

    const x: SpringValue = { value: pointerX, velocity: 0, target: pointerX };
    const y: SpringValue = { value: pointerY + FREE_OFFSET_Y, velocity: 0, target: pointerY + FREE_OFFSET_Y };
    const pinX: SpringValue = { ...x };
    const pinY: SpringValue = { ...y };
    let lensX = x.value;
    let lensY = y.value;
    const width: SpringValue = { value: BASE_WIDTH, velocity: 0, target: BASE_WIDTH };
    const height: SpringValue = { value: BASE_HEIGHT, velocity: 0, target: BASE_HEIGHT };
    const snap: SpringValue = { value: 0, velocity: 0, target: 0 };

    type MagneticState = {
      x: SpringValue;
      y: SpringValue;
      appliedX: number;
      appliedY: number;
    };

    const magneticStates = new Map<HTMLElement, MagneticState>();
    let magneticTargets: HTMLElement[] = [];

    const refreshMagneticTargets = () => {
      const next = Array.from(root.querySelectorAll<HTMLElement>(MAGNETIC_SELECTOR))
        .filter((element) => !element.matches(MAGNETIC_OPT_OUT) && !element.closest("[inert], [aria-hidden='true']"));
      const nextSet = new Set(next);
      for (const [element] of magneticStates) {
        if (!nextSet.has(element)) {
          element.style.removeProperty("translate");
          magneticStates.delete(element);
        }
      }
      magneticTargets = next;
      for (const element of magneticTargets) {
        if (!magneticStates.has(element)) {
          magneticStates.set(element, {
            x: { value: 0, velocity: 0, target: 0 },
            y: { value: 0, velocity: 0, target: 0 },
            appliedX: 0,
            appliedY: 0,
          });
        }
      }
    };

    // Every lens stop is a magnet, so this loop now runs over every control on the
    // page. It measures them all before writing any translate: interleaving the two
    // would force a layout per element per frame.
    const updateMagneticTargets = (dt: number) => {
      let settled = true;
      // Measure first, then integrate all springs, then write. The original
      // DOM rect is recovered by subtracting our previously applied translation.
      const measured: Array<{ element: HTMLElement; state: MagneticState; baseLeft: number; baseTop: number; width: number; height: number }> = [];
      for (const element of magneticTargets) {
        const state = magneticStates.get(element);
        if (!state || !element.isConnected) continue;
        const rect = element.getBoundingClientRect();
        measured.push({
          element,
          state,
          baseLeft: rect.left - state.appliedX,
          baseTop: rect.top - state.appliedY,
          width: rect.width,
          height: rect.height,
        });
      }

      const planned = measured.map(item => {
        const { element, state, baseLeft, baseTop, width, height } = item;
        const baseRight = baseLeft + width;
        const baseBottom = baseTop + height;
        const hoverArea = element.classList.contains("loom-portal-brand") ? 22 : 18;
        const inside =
          pointerInside &&
          pointerX >= baseLeft - hoverArea &&
          pointerX <= baseRight + hoverArea &&
          pointerY >= baseTop - hoverArea &&
          pointerY <= baseBottom + hoverArea;

        if (inside) {
          const centerX = baseLeft + width / 2;
          const centerY = baseTop + height / 2;
          const normalizedX = Math.max(-1, Math.min(1, (pointerX - centerX) / Math.max(width / 2, 1)));
          const normalizedY = Math.max(-1, Math.min(1, (pointerY - centerY) / Math.max(height / 2, 1)));
          const distance = magnetAmplitude({
            brand: element.classList.contains("loom-portal-brand"),
            typingSurface: element.matches("input, textarea, select, .loom-input-shell, .loom-code-input, [contenteditable='true']"),
            width,
            height,
            active: activeTarget === element,
          });
          state.x.target = normalizedX * distance;
          state.y.target = normalizedY * distance;
        } else {
          state.x.target = 0;
          state.y.target = 0;
        }

        // Both OAuth buttons keep their own elastic spring. We limit only
        // how far their inner borders can approach during the handoff.
        stepSpring(state.x, dt, 250, 21);
        stepSpring(state.y, dt, 250, 21);
        return {
          ...item,
          nextX: Math.round(state.x.value * 2) / 2,
          nextY: Math.round(state.y.value * 2) / 2,
        };
      });

      // Two neighboring OAuth buttons share a 10px gap. Previously their
      // simultaneous magnetic motion could overlap by 6px, and the refracting
      // lens amplified their touching borders into a false white seam.
      const oauth = planned.filter(item => item.element.matches(".loom-oauth-grid > .loom-oauth-button"));
      for (let index = 0; index < oauth.length - 1; index++) {
        const left = oauth[index];
        const right = oauth[index + 1];
        if (left.element.parentElement !== right.element.parentElement) continue;
        const originalGap = right.baseLeft - (left.baseLeft + left.width);
        const limited = constrainAdjacentMagneticOffsets(left.nextX, right.nextX, originalGap);
        left.nextX = limited.leftX;
        right.nextX = limited.rightX;
      }

      for (const { element, state, nextX, nextY } of planned) {
        if (Math.abs(nextX - state.appliedX) >= 0.49 || Math.abs(nextY - state.appliedY) >= 0.49) {
          state.appliedX = nextX;
          state.appliedY = nextY;
          if (Math.abs(nextX) < 0.125 && Math.abs(nextY) < 0.125 &&
              state.x.target === 0 && state.y.target === 0) {
            element.style.removeProperty("translate");
            state.appliedX = 0;
            state.appliedY = 0;
          } else {
            element.style.setProperty("translate", `${nextX}px ${nextY}px`);
          }
          rasterDirty = true;
          snapDirty = true;
        }
        if (
          Math.abs(state.x.target - state.x.value) >= 0.08 ||
          Math.abs(state.y.target - state.y.value) >= 0.08 ||
          Math.abs(state.x.velocity) >= 0.08 ||
          Math.abs(state.y.velocity) >= 0.08
        ) {
          settled = false;
        }
      }
      return settled;
    };

    refreshMagneticTargets();

    // A field is several elements at once: the title that names it, the shell
    // that draws the box, the input that takes the keystrokes. All of them can
    // sit under one pointer, and the lens has to frame a single box. It frames
    // the control, every time, so a field answers with one lens instead of one
    // per element it happens to be built from.
    const mountOwners = new Map<HTMLElement, HTMLElement>();
    const ownerOf = (element: HTMLElement): HTMLElement => {
      const cached = mountOwners.get(element);
      if (cached?.isConnected) return cached;
      const owner = mountOwner(element);
      mountOwners.set(element, owner);
      return owner;
    };

    // Where the pointer is measured, and where the lens is drawn. They are not
    // the same box: the pointer can be on a field's title, but the lens has to
    // frame the field's input.
    const hitRect = (element: HTMLElement) => element.getBoundingClientRect();
    const mountRect = (element: HTMLElement): DOMRect | null => {
      const owner = ownerOf(element);
      if (owner.dataset.liquidLens === "read") return readingMount(element, pointerX, pointerY);
      return owner.getBoundingClientRect();
    };
    const canMount = (element: HTMLElement) => {
      if (element.matches(":disabled, [aria-disabled='true'], input[type='hidden']") ||
          element.closest("[inert], [aria-hidden='true']")) return false;
      // Every actionable control mounts, including formerly opted-out links.
      if (element.dataset.liquidSnap === "false" && !element.matches(CLICKABLE_SELECTOR)) return false;
      if (element instanceof HTMLLabelElement && (!element.control || element.control.matches(":disabled"))) return false;
      const style = getComputedStyle(element);
      return style.display !== "none" && style.visibility !== "hidden" && style.pointerEvents !== "none";
    };

    const findSnapTarget = () => {
      if (activeTarget?.isConnected && canMount(activeTarget)) {
        // A reading stop follows the line under the pointer. A control keeps its
        // own box, so the pointer can rest on a field's title without the lens
        // letting go of the field.
        const owner = ownerOf(activeTarget);
        const rect = owner.dataset.liquidLens === "read" ? mountRect(activeTarget) : hitRect(activeTarget);
        const style = getComputedStyle(activeTarget);
        if (rect && rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.pointerEvents !== "none" &&
            rectDistance(rect, pointerX, pointerY) <= RELEASE_DISTANCE) return activeTarget;
      }
      activeTarget = null;
      let closest = SNAP_DISTANCE + 0.001;
      for (const candidate of Array.from(root.querySelectorAll<HTMLElement>(SNAP_SELECTOR))) {
        if (candidate.dataset.loomLiquidCursor === "true") continue;
        if (!canMount(candidate)) continue;
        if (candidate.closest("[inert], [aria-hidden='true']")) continue;
        const style = getComputedStyle(candidate);
        if (style.pointerEvents === "none" || style.visibility === "hidden" || style.display === "none") continue;
        const rect = mountRect(candidate);
        if (!rect || rect.width <= 0 || rect.height <= 0) continue;
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
      const previousTarget = activeTarget;
      const target = findSnapTarget();
      // The frame is what matters here: a field's title and its input are two
      // elements but one lens, and the lens must not restart when the pointer
      // crosses from one to the other.
      const previousOwner = previousTarget ? ownerOf(previousTarget) : null;
      const owner = target ? ownerOf(target) : null;
      if (owner !== previousOwner) {
        rasterDirty = true;
        roiLockedTarget = null;
        if (target) {
          if (!previousTarget) {
            pinX.value = lensX;
            pinX.velocity = 0;
            pinY.value = lensY;
            pinY.velocity = 0;
          }
          const rect = mountRect(target)!;
          const frame = paddedMount(ownerOf(target), rect);
          const finalLensWidth = Math.min(Math.max(38, window.innerWidth - 20), frame.width);
          const finalLensHeight = Math.min(Math.max(34, window.innerHeight - 20), frame.height);
          // Allocate the entire capture surface once on snap entry. Growing
          // the canvas each spring frame destroyed the WebGL texture and made
          // the wide Ask input flicker continuously.
          snappedRoiWidth = Math.max(
            snappedRoiWidth,
            Math.min(window.innerWidth, Math.max(
              FREE_ROI_SIZE,
              Math.ceil(Math.max(finalLensWidth, width.value) * 1.08 + 80),
            )),
          );
          snappedRoiHeight = Math.max(
            snappedRoiHeight,
            Math.min(window.innerHeight, Math.max(
              FREE_ROI_SIZE,
              Math.ceil(Math.max(finalLensHeight, height.value) * 1.08 + 80),
            )),
          );
        }
      }
      if (target) {
        const rect = mountRect(target)!;
        const frame = paddedMount(ownerOf(target), rect);
        pinX.target = rect.left + rect.width / 2;
        pinY.target = rect.top + rect.height / 2;
        width.target = Math.min(Math.max(38, window.innerWidth - 20), frame.width);
        height.target = Math.min(Math.max(34, window.innerHeight - 20), frame.height);
        snap.target = 1;
      } else {
        width.target = BASE_WIDTH;
        height.target = BASE_HEIGHT;
        snap.target = 0;
      }
    };

    const updateRoi = (lensX: number, lensY: number, lensWidth: number, lensHeight: number) => {
      const target = activeTarget?.isConnected ? activeTarget : null;
      // Hold the snapped ROI at its final size for the entire morph and release.
      // Resizing the backing canvas while width/height springs are moving invalidates
      // the WebGL texture and was the source of the visible flashing.
      const holdSnappedRoi =
        Boolean(target) ||
        snap.value > 0.025 ||
        lensWidth > BASE_WIDTH + 4 ||
        lensHeight > BASE_HEIGHT + 4;
      // Keep capture dimensions fixed throughout the snap/release animation.
      const desiredWidth = holdSnappedRoi ? snappedRoiWidth : FREE_ROI_SIZE;
      const desiredHeight = holdSnappedRoi ? snappedRoiHeight : FREE_ROI_SIZE;

      if (!holdSnappedRoi) {
        snappedRoiWidth = FREE_ROI_SIZE;
        snappedRoiHeight = FREE_ROI_SIZE;
      }

      const boundedWidth = Math.max(1, Math.min(window.innerWidth, desiredWidth));
      const boundedHeight = Math.max(1, Math.min(window.innerHeight, desiredHeight));
      if (boundedWidth !== roiWidth || boundedHeight !== roiHeight) {
        roiWidth = boundedWidth;
        roiHeight = boundedHeight;
        if (resizeSurfaces(roiWidth, roiHeight)) {
          textureReady = false;
          // A new backing store needs a texture in this same frame; capture
          // throttling must not hide the lens for one frame on snap entry.
          lastCapture = Number.NEGATIVE_INFINITY;
          lastRoiLeft = Number.NaN;
          lastRoiTop = Number.NaN;
        }
        roiLeft = Number.NaN;
        roiTop = Number.NaN;
        roiLockedTarget = null;
        rasterDirty = true;
      }

      // Move the captured texture with the cursor and clamp it to the viewport.
      // A zero-width clamp pinned the canvas at (0, 0), clipping the lens
      // whenever its center traveled outside the capture area.
      const minLeft = 0;
      const minTop = 0;
      const maxLeft = Math.max(0, window.innerWidth - roiWidth);
      const maxTop = Math.max(0, window.innerHeight - roiHeight);

      if (target) {
        // The capture follows the framed control, not the element the pointer
        // happened to be over: a field's title and its input are one lens.
        const owner = ownerOf(target);
        const state = magneticStates.get(owner);
        const rect = owner.getBoundingClientRect();
        // Lock the capture window to the target's non-magnetic base position.
        // The button can still wobble inside this texture without dragging the ROI.
        const baseCenterX = rect.left + rect.width / 2 - (state?.appliedX ?? 0);
        const baseCenterY = rect.top + rect.height / 2 - (state?.appliedY ?? 0);
        // The lock follows the target's place on the page. A page that reflows
        // under the cursor (a font that finishes loading, a header that grows)
        // moves the target, and a window locked to the old place samples the
        // page from somewhere else: the control under the lens is then drawn
        // displaced and smeared.
        if (roiLockedTarget !== owner || !Number.isFinite(roiLeft) || !Number.isFinite(roiTop) ||
            Math.abs(baseCenterX - roiLockedBaseX) >= 1 || Math.abs(baseCenterY - roiLockedBaseY) >= 1) {
          roiLeft = Math.round(Math.max(minLeft, Math.min(maxLeft, baseCenterX - roiWidth / 2)));
          roiTop = Math.round(Math.max(minTop, Math.min(maxTop, baseCenterY - roiHeight / 2)));
          roiLockedTarget = owner;
          roiLockedBaseX = baseCenterX;
          roiLockedBaseY = baseCenterY;
          rasterDirty = true;
        }
      } else {
        roiLockedTarget = null;
        roiLockedBaseX = Number.NaN;
        roiLockedBaseY = Number.NaN;
        if (!Number.isFinite(roiLeft) || !Number.isFinite(roiTop)) {
          roiLeft = Math.round(Math.max(minLeft, Math.min(maxLeft, lensX - roiWidth / 2)));
          roiTop = Math.round(Math.max(minTop, Math.min(maxTop, lensY - roiHeight / 2)));
          rasterDirty = true;
          return;
        }
      }

      // The target center is only an initialization hint. During the snap
      // transition, the ROI must still track the actual animated lens.
      const halfW = lensWidth / 2;
      const halfH = lensHeight / 2;
      const marginX = Math.max(8, Math.min(FREE_ROI_PADDING, (roiWidth - lensWidth) / 2 - 8));
      const marginY = Math.max(8, Math.min(FREE_ROI_PADDING, (roiHeight - lensHeight) / 2 - 8));
      const minX = roiLeft + halfW + marginX;
      const maxX = roiLeft + roiWidth - halfW - marginX;
      const minY = roiTop + halfH + marginY;
      const maxY = roiTop + roiHeight - halfH - marginY;

      let nextLeft = roiLeft;
      let nextTop = roiTop;
      if (lensX < minX) nextLeft -= Math.max(ROI_DEADZONE, minX - lensX);
      else if (lensX > maxX) nextLeft += Math.max(ROI_DEADZONE, lensX - maxX);
      if (lensY < minY) nextTop -= Math.max(ROI_DEADZONE, minY - lensY);
      else if (lensY > maxY) nextTop += Math.max(ROI_DEADZONE, lensY - maxY);

      // A deadzone is useful while stationary, but it is not enough during a
      // long snap-to-snap spring transition: enforce complete containment of
      // the visible lens, including the shader's small edge deformation.
      const lensLeft = Math.max(0, lensX - lensWidth * 0.52 - 12);
      const lensRight = Math.min(window.innerWidth, lensX + lensWidth * 0.52 + 12);
      const lensTop = Math.max(0, lensY - lensHeight * 0.52 - 12);
      const lensBottom = Math.min(window.innerHeight, lensY + lensHeight * 0.52 + 12);
      nextLeft = lensRight - lensLeft <= roiWidth
        ? Math.min(lensLeft, Math.max(lensRight - roiWidth, nextLeft))
        : (lensLeft + lensRight - roiWidth) / 2;
      nextTop = lensBottom - lensTop <= roiHeight
        ? Math.min(lensTop, Math.max(lensBottom - roiHeight, nextTop))
        : (lensTop + lensBottom - roiHeight) / 2;

      nextLeft = Math.round(Math.max(minLeft, Math.min(maxLeft, nextLeft)));
      nextTop = Math.round(Math.max(minTop, Math.min(maxTop, nextTop)));
      if (nextLeft !== roiLeft || nextTop !== roiTop) {
        roiLeft = nextLeft;
        roiTop = nextTop;
        rasterDirty = true;
      }
    };
    const uploadTexture = (roiLeft: number, roiTop: number, now: number) => {
      const moved = Math.abs(roiLeft - lastRoiLeft) >= 0.75 || Math.abs(roiTop - lastRoiTop) >= 0.75;
      if (!rasterDirty && !moved) return;
      const minCaptureInterval = activeTarget ? 16 : 32;
      // A capture window that moved is refreshed in the same frame. The canvas
      // is drawn at the new position, so a texture taken at the old one shows
      // the page from somewhere else and the control under the cursor reads as
      // flickering while the lens travels.
      if (!moved && now - lastCapture < minCaptureInterval) return;
      lastCapture = now;
      lastRoiLeft = roiLeft;
      lastRoiTop = roiTop;
      rasterDirty = false;
      if (!rasterizePortal(root, capture, scratch, wallpaper.complete ? wallpaper : null, roiLeft, roiTop, roiWidth, roiHeight, dpr, caretVisible)) return;
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
      if (now < refractionMotionUntil) {
        // Follow the actual CSS animation, including ::after pseudo-elements.
        // A short tail captures the final frame even if transitionend fires
        // before the next texture-upload interval.
        if ((activeTarget ?? root).getAnimations({ subtree: true }).some(animation => animation.playState === "running")) {
          refractionMotionUntil = Math.max(refractionMotionUntil, now + 80);
        }
        rasterDirty = true;
      }
      const magneticSettled = updateMagneticTargets(dt);
      updateTargets();

      const snapping = Boolean(activeTarget) || snap.target > 0.001;
      x.target = pointerX;
      y.target = pointerY + FREE_OFFSET_Y;
      stepSpring(x, dt, 500, 40);
      stepSpring(y, dt, 500, 40);
      stepSpring(pinX, dt, 300, 25);
      stepSpring(pinY, dt, 300, 25);
      stepSpring(width, dt, snapping ? 235 : 310, snapping ? 19 : 32);
      stepSpring(height, dt, snapping ? 235 : 310, snapping ? 19 : 32);
      stepSpring(snap, dt, 220, 18);
      // Firm compression on contact, then one softer elastic release.
      stepSpring(pressure, dt, pressed ? 620 : 400, pressed ? 38 : 23);
      const deformation = Math.max(-0.22, Math.min(1.08, pressure.value));

      lensX = blendAnchor(x.value, pinX.value, snap.value);
      lensY = blendAnchor(y.value, pinY.value, snap.value);
      updateRoi(lensX, lensY, width.value, height.value);
      uploadTexture(roiLeft, roiTop, now);

      canvas.style.transform = `translate3d(${roiLeft}px, ${roiTop}px, 0)`;
      dot.style.transform = `translate3d(${pointerX - 1.75}px, ${pointerY - 1.75}px, 0)`;
      canvas.style.opacity = pointerInside && textureReady ? "1" : "0";
      dot.style.opacity = pointerInside ? (snap.value > 0.4 ? ".42" : ".86") : "0";

      if (textureReady) {
        gl.viewport(0, 0, canvas.width, canvas.height);
        gl.bindTexture(gl.TEXTURE_2D, texture);
        drawReferenceLens({
          roiWidth, roiHeight,
          centerX: lensX - roiLeft, centerY: lensY - roiTop,
          width: width.value * (1 + 0.025 * deformation),
          height: height.value * (1 - 0.085 * deformation),
          screenDpr: window.devicePixelRatio || 1,
          time: now / 1000,
        });
      }

      const settled =
        Math.abs(x.target - x.value) < 0.08 &&
        Math.abs(y.target - y.value) < 0.08 &&
        Math.abs(width.target - width.value) < 0.08 &&
        Math.abs(height.target - height.value) < 0.08 &&
        Math.abs(snap.target - snap.value) < 0.002 &&
        Math.abs(snap.velocity) < 0.002 &&
        (!activeTarget || (
          Math.abs(pinX.target - pinX.value) < 0.08 && Math.abs(pinX.velocity) < 0.08 &&
          Math.abs(pinY.target - pinY.value) < 0.08 && Math.abs(pinY.velocity) < 0.08
        )) &&
        Math.abs(x.velocity) < 0.08 &&
        Math.abs(y.velocity) < 0.08 &&
        Math.abs(width.velocity) < 0.08 &&
        Math.abs(height.velocity) < 0.08 &&
        Math.abs(pressure.target - pressure.value) < 0.001 &&
        Math.abs(pressure.velocity) < 0.01 &&
        magneticSettled &&
        now >= refractionMotionUntil;

      // Pointer/input/layout events wake the loop. A stationary cursor does
      // not need continuous DOM measurements and WebGL draws once springs settle.
      if (settled && !rasterDirty && !snapDirty) {
        running = false;
        return;
      }
      raf = window.requestAnimationFrame(frame);
    };

    const wake = () => ensureFrame();

    const refreshAnimatedRefraction = (event: Event) => {
      const source = event.target;
      if (!(source instanceof Node) || !activeTarget?.isConnected ||
          (source !== activeTarget && !activeTarget.contains(source))) return;
      refractionMotionUntil = Math.max(refractionMotionUntil, performance.now() + 360);
      rasterDirty = true;
      wake();
    };

    // The lens is a pointer-events:none visual overlay. When it snaps from
    // outside the DOM hit box, the browser would otherwise click empty space.
    // Redirect only a real primary-pointer click near the CURRENT snap target.
    // Leave clicks within the element (and all keyboard clicks) fully native.
    const handleSnappedClick = (event: MouseEvent) => {
      if (!event.isTrusted || event.defaultPrevented || event.button !== 0 ||
          event.detail === 0 || !pointerInside ||
          event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      if (Math.hypot(event.clientX - pointerX, event.clientY - pointerY) > 2) return;

      const candidate = findSnapTarget();
      if (!candidate || !candidate.isConnected || candidate.closest("[inert], [aria-hidden='true']")) return;
      if (rectDistance(candidate.getBoundingClientRect(), event.clientX, event.clientY) > RELEASE_DISTANCE) return;

      const actionSelector = CLICKABLE_SELECTOR;
      // The lens framed the control, so the click belongs to the control: a
      // field's title and its input are one thing to click.
      const owner = ownerOf(candidate);
      const action = owner.matches(actionSelector)
        ? owner
        : owner.querySelector<HTMLElement>(actionSelector);
      if (!action || action.matches(":disabled, [aria-disabled='true']") ||
          action.closest("[inert], [aria-hidden='true']")) return;
      const source = event.target;
      if (source instanceof Node && candidate.contains(source)) return;
      // A neighboring button, native label or modal must keep its own click.
      const nativeAction = source instanceof Element
        ? source.closest(CLICKABLE_SELECTOR)
        : null;
      if (nativeAction && !candidate.contains(nativeAction)) return;
      if (source instanceof Element && source.closest("[role='dialog'], .auth-modal.is-open") &&
          !candidate.contains(source)) return;

      event.preventDefault();
      event.stopImmediatePropagation();
      if (action.matches("input, textarea, select")) action.focus({ preventScroll: true });
      // HTMLElement.click() triggers the existing React/DOM/native handlers,
      // including the switch and navigation, exactly once. Synthetic clicks
      // do not pass the isTrusted guard above, so they cannot recurse.
      action.click();
    };

    const handlePointerMove = (event: PointerEvent) => {
      pointerX = event.clientX;
      pointerY = event.clientY;
      pointerInside = true;
      snapDirty = true;
      wake();
    };
    const handlePointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      pressed = true;
      pressure.target = 1;
      // A brief tap still has a visible contact phase before its release.
      pressure.velocity = Math.max(pressure.velocity, 5);
      rasterDirty = true;
      snapDirty = true;
      wake();
    };
    const handlePointerUp = () => {
      if (!pressed) return;
      pressed = false;
      pressure.target = 0;
      pressure.value = Math.max(pressure.value, 0.22);
      pressure.velocity = Math.min(pressure.velocity, -3.5);
      rasterDirty = true;
      snapDirty = true;
      wake();
    };
    const handlePointerLeave = () => {
      pointerInside = false;
      pressed = false;
      pressure.target = 0;
      activeTarget = null;
      snap.target = 0;
      snapDirty = true;
      wake();
    };
    const handlePointerEnter = () => { pointerInside = true; snapDirty = true; wake(); };
    const handleScroll = () => { rasterDirty = true; snapDirty = true; wake(); };
    // Editing state (focus, keyboard selection, drag-select and Ctrl+A) lives
    // inside the browser's native text control and is not observable through
    // MutationObserver. Wake a capture whenever it changes.
    const handleInput = () => { caretVisible = true; rasterDirty = true; wake(); };
    const handleSelection = () => {
      const field = document.activeElement;
      if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) {
        if (!root.contains(field)) return;
        caretVisible = true;
        rasterDirty = true;
        wake();
      }
    };
    // Native caret blinking never mutates the DOM. A low-frequency tick redraws
    // just while a focused field is under the active glass lens; it does not
    // keep the full WebGL animation loop running elsewhere on the page.
    const caretBlinkTimer = window.setInterval(() => {
      const field = document.activeElement;
      if (!pointerInside || !(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) ||
          !root.contains(field) || !field.isConnected) return;
      const distance = rectDistance(field.getBoundingClientRect(), lensX, lensY);
      if (distance > 50 || field.selectionStart !== field.selectionEnd) return;
      caretVisible = !caretVisible;
      rasterDirty = true;
      wake();
    }, 530);
    const handleResize = () => {
      roiLeft = Number.NaN;
      roiTop = Number.NaN;
      roiLockedTarget = null;
      rasterDirty = true;
      snapDirty = true;
      wake();
    };

    const observer = new MutationObserver((mutations) => {
      refreshMagneticTargets();
      // A field can gain or lose its control; the resolved owners are then stale.
      mountOwners.clear();
      if (mutations.some(mutation => mutation.attributeName?.includes("theme"))) {
        refractionMotionUntil = Math.max(refractionMotionUntil, performance.now() + 360);
      }
      // Theme switches animate their pseudo-element after aria-checked changes.
      // Observing only the mutation captures its initial frame, not the slide.
      if (mutations.some(mutation =>
        mutation.attributeName === "aria-checked" &&
        mutation.target instanceof HTMLElement &&
        mutation.target.getAttribute("role") === "switch" &&
        activeTarget === mutation.target
      )) {
        refractionMotionUntil = Math.max(refractionMotionUntil, performance.now() + 360);
      }
      rasterDirty = true;
      snapDirty = true;
      wake();
    });
    observer.observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["type", "value", "placeholder", "aria-checked", "class", "data-loom-site-theme", "data-theme"] });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-loom-theme"] });
    const resizeObserver = new ResizeObserver(() => { rasterDirty = true; snapDirty = true; wake(); });
    resizeObserver.observe(root);

    wallpaper.addEventListener("load", () => { rasterDirty = true; wake(); }, { once: true });
    document.fonts?.ready.then(() => { rasterDirty = true; wake(); }).catch(() => {});

    window.addEventListener("click", handleSnappedClick, true);
    root.addEventListener("transitionrun", refreshAnimatedRefraction);
    root.addEventListener("animationstart", refreshAnimatedRefraction);
    root.addEventListener("pointermove", handlePointerMove);
    root.addEventListener("pointerdown", handlePointerDown);
    root.addEventListener("pointerup", handlePointerUp);
    root.addEventListener("pointercancel", handlePointerUp);
    root.addEventListener("pointerleave", handlePointerLeave);
    root.addEventListener("pointerenter", handlePointerEnter);
    root.addEventListener("focusin", handleSelection, true);
    root.addEventListener("focusout", handleSelection, true);
    root.addEventListener("keyup", handleSelection, true);
    root.addEventListener("select", handleSelection, true);
    document.addEventListener("selectionchange", handleSelection);
    root.addEventListener("input", handleInput, true);
    root.addEventListener("change", handleInput, true);
    root.addEventListener("load", handleInput, true);
    window.addEventListener("scroll", handleScroll, true);
    window.addEventListener("resize", handleResize);
    window.addEventListener("blur", handlePointerLeave);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerUp);
    document.documentElement.classList.add("loom-liquid-cursor-active");

    ensureFrame();

    return () => {
      window.cancelAnimationFrame(raf);
      window.clearInterval(caretBlinkTimer);
      observer.disconnect();
      resizeObserver.disconnect();
      window.removeEventListener("click", handleSnappedClick, true);
      root.removeEventListener("transitionrun", refreshAnimatedRefraction);
      root.removeEventListener("animationstart", refreshAnimatedRefraction);
      root.removeEventListener("pointermove", handlePointerMove);
      root.removeEventListener("pointerdown", handlePointerDown);
      root.removeEventListener("pointerup", handlePointerUp);
      root.removeEventListener("pointercancel", handlePointerUp);
      root.removeEventListener("pointerleave", handlePointerLeave);
      root.removeEventListener("pointerenter", handlePointerEnter);
      root.removeEventListener("focusin", handleSelection, true);
      root.removeEventListener("focusout", handleSelection, true);
      root.removeEventListener("keyup", handleSelection, true);
      root.removeEventListener("select", handleSelection, true);
      document.removeEventListener("selectionchange", handleSelection);
      root.removeEventListener("input", handleInput, true);
      root.removeEventListener("change", handleInput, true);
      root.removeEventListener("load", handleInput, true);
      window.removeEventListener("scroll", handleScroll, true);
      window.removeEventListener("resize", handleResize);
      window.removeEventListener("blur", handlePointerLeave);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerUp);
      document.documentElement.classList.remove("loom-liquid-cursor-active");
      for (const element of magneticTargets) element.style.removeProperty("translate");
      magneticStates.clear();
      gl.deleteTexture(texture);
      gl.deleteBuffer(position);
      gl.deleteBuffer(uv);
      gl.deleteProgram(program);
    };
  }, [reducedMotion]);

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
