/* Native input caret and ::selection are browser-painted, not DOM descendants.
 * The liquid lens captures a 2D approximation of the page and must explicitly
 * reproduce these editing affordances, after the field's background is painted.
 * Only use the user's already-rendered value: password glyphs remain masked. */
type EditableField = HTMLInputElement | HTMLTextAreaElement;
type TextLine = { text: string; start: number; end: number };

function number(value: string, fallback = 0) {
  const n = Number.parseFloat(value);
  return Number.isFinite(n) ? n : fallback;
}

function isPaintedColor(color: string) {
  return Boolean(color) && color !== "transparent" &&
    color !== "rgba(0, 0, 0, 0)" && color !== "rgba(0,0,0,0)" &&
    color !== "auto";
}

function wrapEditableLines(text: string, measure: (text: string) => number, width: number): TextLine[] {
  const lines: TextLine[] = [];
  let start = 0;
  let line = "";
  let offset = 0;
  for (const character of text) {
    if (character === "\n") {
      lines.push({ text: line, start, end: offset });
      offset++;
      start = offset;
      line = "";
      continue;
    }
    if (line && measure(line + character) > width) {
      lines.push({ text: line, start, end: offset });
      start = offset;
      line = "";
    }
    line += character;
    offset += character.length;
  }
  lines.push({ text: line, start, end: offset });
  return lines;
}

export function drawEditableField(
  ctx: CanvasRenderingContext2D,
  el: EditableField,
  rect: DOMRect,
  style: CSSStyleDeclaration,
  roiLeft: number,
  roiTop: number,
  opacity: number,
  caretOn: boolean,
) {
  const isTextarea = el instanceof HTMLTextAreaElement;
  const value = el.value;
  // Do not expose the underlying password through the capture texture.
  const visibleValue = !isTextarea && el instanceof HTMLInputElement && el.type === "password"
    ? "•".repeat(value.length) : value;
  const placeholder = !value ? el.placeholder : "";
  const displayed = visibleValue || placeholder;
  const placeholderStyle = !value ? getComputedStyle(el, "::placeholder") : style;

  ctx.save();
  ctx.globalAlpha = opacity;
  ctx.font = `${style.fontStyle || "normal"} ${style.fontWeight || "400"} ${style.fontSize || "16px"} ${style.fontFamily || "sans-serif"}`;
  const letterAware = ctx as CanvasRenderingContext2D & { letterSpacing?: string };
  if ("letterSpacing" in letterAware) letterAware.letterSpacing = style.letterSpacing === "normal" ? "0px" : style.letterSpacing;
  ctx.textBaseline = "middle";
  const measure = (text: string) => ctx.measureText(text).width;
  const localX = rect.left - roiLeft;
  const localY = rect.top - roiTop;
  const leftPad = number(style.paddingLeft) + number(style.borderLeftWidth);
  const rightPad = number(style.paddingRight) + number(style.borderRightWidth);
  const topPad = number(style.paddingTop) + number(style.borderTopWidth);
  const bottomPad = number(style.paddingBottom) + number(style.borderBottomWidth);
  const textWidth = Math.max(1, rect.width - leftPad - rightPad);
  const textHeight = Math.max(1, rect.height - topPad - bottomPad);
  const fontSize = number(style.fontSize, 16);
  const cssLineHeight = number(style.lineHeight);
  const lineHeight = cssLineHeight > 0 ? cssLineHeight : fontSize * 1.2;
  const selectStyle = getComputedStyle(el, "::selection");
  const selectionBackground = isPaintedColor(selectStyle.backgroundColor)
    ? selectStyle.backgroundColor : "rgba(77, 128, 215, 0.65)";
  const selectionInk = isPaintedColor(selectStyle.color) ? selectStyle.color : style.color;
  const active = document.activeElement === el;

  let selectionStart = 0;
  let selectionEnd = 0;
  if (active) {
    try {
      // Unsupported input types (notably type=email) throw for selectionStart.
      // Editable email inputs in Loom use inputMode=email with type=text.
      selectionStart = el.selectionStart ?? value.length;
      selectionEnd = el.selectionEnd ?? selectionStart;
    } catch {
      selectionStart = selectionEnd = value.length;
    }
  }
  const rangeStart = Math.max(0, Math.min(value.length, Math.min(selectionStart, selectionEnd)));
  const rangeEnd = Math.max(0, Math.min(value.length, Math.max(selectionStart, selectionEnd)));
  const selected = active && rangeEnd > rangeStart;
  const lines: TextLine[] = isTextarea
    ? wrapEditableLines(displayed, measure, textWidth)
    : [{ text: displayed, start: 0, end: displayed.length }];

  // Native text controls clip to their content box, independent of their
  // decorative parent shell's border radius.
  ctx.beginPath();
  ctx.rect(localX + leftPad, localY + topPad, textWidth, textHeight);
  ctx.clip();

  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const y = isTextarea
      ? localY + topPad + lineHeight * (index + 0.5) - el.scrollTop
      : localY + rect.height / 2 - el.scrollTop;
    if (y + lineHeight / 2 < localY + topPad || y - lineHeight / 2 > localY + rect.height - bottomPad) continue;

    let x = localX + leftPad - el.scrollLeft;
    if (!isTextarea) {
      const alignment = style.textAlign === "start" ? (style.direction === "rtl" ? "right" : "left")
        : style.textAlign === "end" ? (style.direction === "rtl" ? "left" : "right") : style.textAlign;
      if (alignment === "center") x += Math.max(0, (textWidth - measure(line.text)) / 2);
      if (alignment === "right") x += Math.max(0, textWidth - measure(line.text));
    }

    const start = Math.max(line.start, rangeStart);
    const end = Math.min(line.end, rangeEnd);
    const selectionX = x + measure(line.text.slice(0, Math.max(0, start - line.start)));
    const selectionWidth = end > start
      ? measure(line.text.slice(Math.max(0, start - line.start), end - line.start))
      : 0;

    if (selected && end > start) {
      ctx.fillStyle = selectionBackground;
      ctx.fillRect(selectionX, y - lineHeight / 2, Math.max(1, selectionWidth), lineHeight);
    }

    if (line.text) {
      ctx.fillStyle = isPaintedColor(placeholderStyle.color) ? placeholderStyle.color : style.color;
      ctx.globalAlpha = opacity * (value ? 1 : Math.max(0, Math.min(1, number(placeholderStyle.opacity, 1))));
      ctx.fillText(line.text, x, y);
      if (selected && end > start) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(selectionX, y - lineHeight / 2, Math.max(1, selectionWidth), lineHeight);
        ctx.clip();
        ctx.fillStyle = selectionInk;
        ctx.fillText(line.text, x, y);
        ctx.restore();
      }
      ctx.globalAlpha = opacity;
    }

    // Draw the editing caret above the text. Browsers draw it themselves, so
    // it does not appear in canvas-based capture without this step.
    const caretInLine = selectionStart >= line.start &&
      (selectionStart < line.end || (selectionStart === line.end &&
        (index === lines.length - 1 || !isTextarea || displayed[line.end] !== "\n")));
    if (active && !selected && caretOn && caretInLine) {
      const caretColor = isPaintedColor(style.caretColor) ? style.caretColor : style.color;
      if (isPaintedColor(caretColor)) {
        const caretX = x + measure(line.text.slice(0, Math.max(0, selectionStart - line.start)));
        ctx.fillStyle = caretColor;
        ctx.fillRect(caretX, y - Math.min(lineHeight, textHeight) * 0.44, 1.5, Math.min(lineHeight, textHeight) * 0.88);
      }
    }
  }
  ctx.restore();
}
