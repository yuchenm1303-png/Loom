import { drawEditableField } from "./portalLiquidEditable";

function intersects(rect: DOMRect, left: number, top: number, width: number, height: number) {
  return rect.right >= left && rect.left <= left + width && rect.bottom >= top && rect.top <= top + height;
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

// The lens samples the page, so it has to respect the same stacking order the
// page paints in: the sticky header sits above the sections that scroll under
// it (z-index 5 against auto), and painting the sections last drew the page
// through the bar whenever the pointer was near the top.
function stackingKey(el: HTMLElement) {
  const style = getComputedStyle(el);
  if (style.position === "static") return 0;
  const value = style.zIndex;
  if (!value || value === "auto") return 0;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

// Document order, except where an explicit z-index says otherwise. Stable, so
// everything with the same stacking key keeps painting in DOM order.
function paintOrder(el: HTMLElement): ChildNode[] {
  const children = Array.from(el.childNodes);
  if (children.length < 2) return children;
  const keyed = children.map((child, index) => ({
    child,
    index,
    key: child instanceof HTMLElement ? stackingKey(child) : 0,
  }));
  if (keyed.every((item) => item.key === 0)) return children;
  keyed.sort((a, b) => a.key - b.key || a.index - b.index);
  return keyed.map((item) => item.child);
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
  let dx = boxX;
  let dy = boxY;
  let dw = rect.width;
  let dh = rect.height;

  if (fit === "contain") {
    const scale = Math.min(rect.width / sw, rect.height / sh);
    dw = sw * scale;
    dh = sh * scale;
    dx += (rect.width - dw) / 2;
    dy += (rect.height - dh) / 2;
  } else if (fit === "cover") {
    const boxRatio = rect.width / Math.max(rect.height, 1);
    const imageRatio = img.naturalWidth / Math.max(img.naturalHeight, 1);
    const cropWidth = imageRatio > boxRatio;
    if (cropWidth) {
      sw = img.naturalHeight * boxRatio;
      sx = (img.naturalWidth - sw) / 2;
    } else {
      sh = img.naturalWidth / boxRatio;
      sy = (img.naturalHeight - sh) / 2;
    }
  }

  try {
    ctx.drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh);
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
  roiWidth: number,
  roiHeight: number,
) {
  ctx.fillStyle = getComputedStyle(document.body).backgroundColor || "#1f1e1d";
  ctx.fillRect(0, 0, roiWidth, roiHeight);
  if (!image) return;
  if (image?.complete && image.naturalWidth > 0 && image.naturalHeight > 0) {
    const scale = Math.max(viewportWidth / image.naturalWidth, viewportHeight / image.naturalHeight);
    const drawnWidth = image.naturalWidth * scale;
    const drawnHeight = image.naturalHeight * scale;
    const pageX = (viewportWidth - drawnWidth) * 0.54;
    const pageY = (viewportHeight - drawnHeight) * 0.5;

    const srcX = Math.max(0, (roiLeft - pageX) / scale);
    const srcY = Math.max(0, (roiTop - pageY) / scale);
    const srcRight = Math.min(image.naturalWidth, (roiLeft + roiWidth - pageX) / scale);
    const srcBottom = Math.min(image.naturalHeight, (roiTop + roiHeight - pageY) / scale);
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
  ctx.fillRect(0, 0, roiWidth, roiHeight);

  const cx = viewportWidth * 0.5 - roiLeft;
  const cy = viewportHeight * 0.34 - roiTop;
  const radius = Math.max(viewportWidth, viewportHeight) * 0.72;
  const vignette = ctx.createRadialGradient(cx, cy, radius * 0.24, cx, cy, radius);
  vignette.addColorStop(0, "rgba(3,8,14,0)");
  vignette.addColorStop(1, "rgba(3,8,14,.22)");
  ctx.fillStyle = vignette;
  ctx.fillRect(0, 0, roiWidth, roiHeight);
}

function drawTextNode(
  ctx: CanvasRenderingContext2D,
  node: Text,
  style: CSSStyleDeclaration,
  roiLeft: number,
  roiTop: number,
  roiWidth: number,
  roiHeight: number,
  inheritedOpacity: number,
) {
  const value = node.data;
  if (!value.trim()) return;
  const color = style.color;
  if (!visibleColor(color)) return;
  // A whitespace regex treats an entire Chinese paragraph as one word and
  // redraws the whole paragraph into every wrapped line rectangle.
  const matches = Array.from(new Intl.Segmenter(undefined, { granularity: "word" }).segment(value))
    .filter(part => part.segment.trim());
  if (!matches.length) return;

  ctx.save();
  ctx.globalAlpha = inheritedOpacity;
  ctx.fillStyle = color;
  ctx.font = `${style.fontStyle || "normal"} ${style.fontWeight || "400"} ${style.fontSize || "16px"} ${style.fontFamily || "sans-serif"}`;
  ctx.textBaseline = "alphabetic";
  const letterAware = ctx as CanvasRenderingContext2D & { letterSpacing?: string };
  if ("letterSpacing" in letterAware) letterAware.letterSpacing = style.letterSpacing;

  const transform = style.textTransform;
  const drawPart = (index: number, textValue: string) => {
    const range = document.createRange();
    try {
      range.setStart(node, index);
      range.setEnd(node, index + textValue.length);
      const rects = Array.from(range.getClientRects());
      if (rects.length > 1 && Array.from(textValue).length > 1) {
        // A Chinese word can break between characters. Paint each fragment at
        // its actual line location rather than repeating the word on both.
        let offset = index;
        for (const character of Array.from(textValue)) {
          drawPart(offset, character);
          offset += character.length;
        }
        return;
      }
      for (const rect of rects) {
        if (!intersects(rect, roiLeft, roiTop, roiWidth, roiHeight)) continue;
        let text = textValue;
        if (!text) continue;
        if (transform === "uppercase") text = text.toUpperCase();
        if (transform === "lowercase") text = text.toLowerCase();
        if (transform === "capitalize") text = text.replace(/\b\w/g, (part) => part.toUpperCase());
        const metrics = ctx.measureText(text);
        const ascent = metrics.fontBoundingBoxAscent ?? cssNumber(style.fontSize, 16) * 0.8;
        const descent = metrics.fontBoundingBoxDescent ?? cssNumber(style.fontSize, 16) * 0.2;
        const baseline = rect.top - roiTop + (rect.height - ascent - descent) / 2 + ascent;
        ctx.fillText(text, rect.left - roiLeft, baseline, Math.max(rect.width + 2, 1));
      }
    } catch {
      // React may update a text node between range reads.
    } finally {
      range.detach();
    }
  };
  for (const match of matches) drawPart(match.index, match.segment);
  ctx.restore();
}

function drawSvgIcon(ctx: CanvasRenderingContext2D, svg: SVGSVGElement, roiLeft: number, roiTop: number, opacity: number) {
  for (const shape of Array.from(svg.querySelectorAll<SVGGeometryElement>("path, rect, circle, ellipse, line, polyline, polygon"))) {
    const style = getComputedStyle(shape);
    const matrix = shape.getScreenCTM();
    if (!matrix || style.display === "none" || style.visibility === "hidden") continue;
    const number = (name: string) => cssNumber(shape.getAttribute(name) ?? "0");
    const path = new Path2D(shape.tagName === "path" ? shape.getAttribute("d") ?? "" : undefined);
    switch (shape.tagName) {
      case "rect": path.roundRect(number("x"), number("y"), number("width"), number("height"), { x: number("rx"), y: shape.hasAttribute("ry") ? number("ry") : number("rx") }); break;
      case "circle": path.arc(number("cx"), number("cy"), number("r"), 0, Math.PI * 2); break;
      case "ellipse": path.ellipse(number("cx"), number("cy"), number("rx"), number("ry"), 0, 0, Math.PI * 2); break;
      case "line": path.moveTo(number("x1"), number("y1")); path.lineTo(number("x2"), number("y2")); break;
      case "polyline":
      case "polygon": {
        const points = (shape as SVGPolylineElement).points;
        for (let i = 0; i < points.numberOfItems; i++) {
          const point = points.getItem(i);
          if (i === 0) path.moveTo(point.x, point.y);
          else path.lineTo(point.x, point.y);
        }
        if (shape.tagName === "polygon") path.closePath();
        break;
      }
    }
    ctx.save();
    ctx.translate(-roiLeft, -roiTop);
    ctx.transform(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f);
    let alpha = opacity;
    for (let element: Element | null = shape; element && element !== svg; element = element.parentElement) {
      alpha *= cssNumber(getComputedStyle(element).opacity, 1);
    }
    ctx.globalAlpha = alpha * cssNumber(style.fillOpacity, 1);
    if (style.fill !== "none") {
      ctx.fillStyle = style.fill === "currentcolor" ? style.color : style.fill;
      ctx.fill(path, style.fillRule === "evenodd" ? "evenodd" : "nonzero");
    }
    if (style.stroke !== "none") {
      ctx.globalAlpha = alpha * cssNumber(style.strokeOpacity, 1);
      ctx.strokeStyle = style.stroke === "currentcolor" ? style.color : style.stroke;
      ctx.lineWidth = cssNumber(style.strokeWidth, 1);
      ctx.lineCap = style.strokeLinecap as CanvasLineCap;
      ctx.lineJoin = style.strokeLinejoin as CanvasLineJoin;
      ctx.miterLimit = cssNumber(style.strokeMiterlimit, 4);
      ctx.setLineDash(style.strokeDasharray === "none" ? [] : style.strokeDasharray.split(/[ ,]+/).map(value => cssNumber(value)));
      ctx.lineDashOffset = cssNumber(style.strokeDashoffset);
      ctx.stroke(path);
    }
    ctx.restore();
  }
}

function parseBackdropBlur(style: CSSStyleDeclaration) {
  const extended = style as CSSStyleDeclaration & { webkitBackdropFilter?: string };
  const raw = style.backdropFilter || extended.webkitBackdropFilter || "";
  const match = raw.match(/blur\(([\d.]+)px\)/i);
  return match ? Math.min(24, cssNumber(match[1])) : 0;
}

// CSS pseudo-elements do not appear in childNodes, but controls such as the
// landing-page theme switch draw their dark thumb entirely with ::after.
// Recreate that painted thumb in the lens texture instead of sampling just
// the (white) switch track.
function drawSwitchPseudo(
  ctx: CanvasRenderingContext2D,
  el: HTMLElement,
  rect: DOMRect,
  roiLeft: number,
  roiTop: number,
  opacity: number,
) {
  if (el.getAttribute("role") !== "switch") return;
  const pseudo = getComputedStyle(el, "::after");
  if (pseudo.content === "none" || pseudo.content === "normal" ||
      pseudo.display === "none" || pseudo.visibility === "hidden" ||
      !visibleColor(pseudo.backgroundColor)) return;
  const width = cssNumber(pseudo.width);
  const height = cssNumber(pseudo.height);
  if (width <= 0 || height <= 0) return;

  const hostStyle = getComputedStyle(el);
  const left = pseudo.left !== "auto"
    ? cssNumber(hostStyle.borderLeftWidth) + cssNumber(pseudo.left)
    : rect.width - cssNumber(hostStyle.borderRightWidth) - cssNumber(pseudo.right) - width;
  const top = pseudo.top !== "auto"
    ? cssNumber(hostStyle.borderTopWidth) + cssNumber(pseudo.top)
    : rect.height - cssNumber(hostStyle.borderBottomWidth) - cssNumber(pseudo.bottom) - height;

  // getComputedStyle resolves translateX() to a matrix; the theme switch
  // animates its thumb using the matrix translation, not changing 'left'.
  let dx = 0;
  let dy = 0;
  if (pseudo.transform && pseudo.transform !== "none") {
    try {
      const matrix = new DOMMatrixReadOnly(pseudo.transform);
      dx = matrix.m41;
      dy = matrix.m42;
    } catch {
      // Unsupported transform: paint at the base position.
    }
  }

  ctx.save();
  ctx.globalAlpha = opacity * Math.max(0, Math.min(1, cssNumber(pseudo.opacity, 1)));
  ctx.fillStyle = pseudo.backgroundColor;
  const pseudoRect = new DOMRect(0, 0, width, height);
  roundedRect(ctx, rect.left - roiLeft + left + dx, rect.top - roiTop + top + dy,
    width, height, radiusFromStyle(pseudo, pseudoRect));
  ctx.fill();
  ctx.restore();
}

export function rasterizePortal(
  root: HTMLElement,
  canvas: HTMLCanvasElement,
  scratch: HTMLCanvasElement,
  wallpaper: HTMLImageElement | null,
  roiLeft: number,
  roiTop: number,
  roiWidth: number,
  roiHeight: number,
  dpr: number,
  caretOn = true,
) {
  const ctx = canvas.getContext("2d", { alpha: true });
  const scratchCtx = scratch.getContext("2d", { alpha: true });
  if (!ctx || !scratchCtx) return false;

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawWallpaper(ctx, wallpaper, roiLeft, roiTop, window.innerWidth, window.innerHeight, roiWidth, roiHeight);

  // The editorial homepage has both the visible shell and a hidden legacy portal.
  // Sample its visible root, not the hidden classic shell, or the lens goes blank.
  const content = root.classList.contains("loom-editorial")
    ? root
    : (root.querySelector<HTMLElement>(".loom-portal-shell") ?? root);

  const renderElement = (el: HTMLElement, parentOpacity: number) => {
    if (el.dataset.loomLiquidCursor === "true" || el.closest("[data-loom-liquid-cursor='true']")) return;
    if (el.closest(".cosmos") || el.classList.contains("beach-wallpaper")) return;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return;
    const ownOpacity = Math.max(0, Math.min(1, cssNumber(style.opacity, 1)));
    const opacity = parentOpacity * ownOpacity;
    if (opacity <= 0.002) return;

    const rect = el.getBoundingClientRect();
    if (!intersects(rect, roiLeft, roiTop, roiWidth, roiHeight)) return;

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

    const borders = [
      [style.borderTopWidth, style.borderTopColor, style.borderTopStyle],
      [style.borderRightWidth, style.borderRightColor, style.borderRightStyle],
      [style.borderBottomWidth, style.borderBottomColor, style.borderBottomStyle],
      [style.borderLeftWidth, style.borderLeftColor, style.borderLeftStyle],
    ];
    const borderWidth = cssNumber(style.borderTopWidth);
    if (borderWidth > 0 && borders.every(border => border.join() === borders[0].join()) &&
        style.borderTopStyle !== "none" && visibleColor(style.borderTopColor)) {
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
    } else {
      // Editorial rules often paint a single edge, not a complete outline.
      borders.forEach(([rawWidth, color, borderStyle], side) => {
        const thickness = cssNumber(rawWidth);
        if (thickness <= 0 || borderStyle === "none" || !visibleColor(color)) return;
        ctx.save();
        ctx.globalAlpha = opacity;
        ctx.fillStyle = color;
        if (side === 0) ctx.fillRect(localX, localY, rect.width, thickness);
        if (side === 1) ctx.fillRect(localX + rect.width - thickness, localY, thickness, rect.height);
        if (side === 2) ctx.fillRect(localX, localY + rect.height - thickness, rect.width, thickness);
        if (side === 3) ctx.fillRect(localX, localY, thickness, rect.height);
        ctx.restore();
      });
    }

    if (el instanceof HTMLImageElement && safeImageForCanvas(el)) {
      ctx.save();
      ctx.globalAlpha = opacity;
      drawImageFit(ctx, el, rect, style, roiLeft, roiTop);
      ctx.restore();
    }

    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
      drawEditableField(ctx, el, rect, style, roiLeft, roiTop, opacity, caretOn);
    }

    ctx.save();
    if ([style.overflowX, style.overflowY].some(overflow => /^(hidden|clip|auto|scroll)$/.test(overflow))) {
      roundedRect(ctx, localX, localY, rect.width, rect.height, radius);
      ctx.clip();
    }
    for (const child of paintOrder(el)) {
      if (child.nodeType === Node.TEXT_NODE) {
        drawTextNode(ctx, child as Text, style, roiLeft, roiTop, roiWidth, roiHeight, opacity);
      } else if (child instanceof HTMLElement) {
        renderElement(child, opacity);
      } else if (child instanceof SVGSVGElement) {
        const svgStyle = getComputedStyle(child);
        if (svgStyle.display !== "none" && svgStyle.visibility !== "hidden" && intersects(child.getBoundingClientRect(), roiLeft, roiTop, roiWidth, roiHeight)) {
          drawSvgIcon(ctx, child, roiLeft, roiTop, opacity * cssNumber(svgStyle.opacity, 1));
        }
      }
    }
    drawSwitchPseudo(ctx, el, rect, roiLeft, roiTop, opacity);
    ctx.restore();
  };

  renderElement(content, 1);
  return true;
}
