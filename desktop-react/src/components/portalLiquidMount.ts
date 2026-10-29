// Mount geometry only. Keep optical source and strength in portalLiquidOptics.

// The controls a field is built from. A field is never one element: a label
// names it, a shell draws the box, an input takes the keystrokes.
const FIELD_CONTROL_SELECTOR = "input:not([type='hidden']), textarea, select";

type MountElement = {
  dataset: { liquidLens?: string; liquidSnapPad?: string };
  tagName?: string;
  /** Only a label has one: the control it names. */
  control?: unknown;
  querySelectorAll(selectors: string): ArrayLike<unknown>;
};

function isFieldControl(node: unknown): boolean {
  const tag = (node as { tagName?: string } | null | undefined)?.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/**
 * The element a mount point stands for.
 *
 * The lens frames one box, and a field offers several: the title that names
 * it, the shell that draws it, the input that takes the keystrokes. Left to
 * itself the lens picked whichever of them the pointer was nearest, so the
 * same field answered with a small lens over its text and a wide one over
 * its title. The field is the control, so every part of it resolves to that
 * control and the field shows one lens.
 */
export function mountOwner<T extends MountElement>(element: T): T {
  // A reading stop is a piece of prose; it frames itself.
  if (element.dataset.liquidLens === "read") return element;
  const wrapped = Array.from(element.querySelectorAll(FIELD_CONTROL_SELECTOR)) as T[];
  if (wrapped.length === 1) return wrapped[0];
  // A label that names a control it does not wrap (a title with an id) still
  // points at that control.
  const labelled = element.control;
  if (isFieldControl(labelled)) return labelled as T;
  return element;
}

export function readingMount(element: HTMLElement, x: number, y: number): DOMRect | null {
  const lines: DOMRect[] = [];
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let node: Node | null;
  while ((node = walker.nextNode())) {
    if (!node.textContent?.trim() || node.parentElement?.closest("time, [aria-hidden='true']")) continue;
    range.selectNodeContents(node);
    for (const rect of Array.from(range.getClientRects())) {
      if (rect.width <= 0 || rect.height <= 0) continue;
      const line = lines.find(line => Math.abs(line.top - rect.top) < Math.min(line.height, rect.height) * 0.45);
      if (line) {
        const right = Math.max(line.right, rect.right);
        const bottom = Math.max(line.bottom, rect.bottom);
        line.x = Math.min(line.left, rect.left); line.y = Math.min(line.top, rect.top);
        line.width = right - line.x; line.height = bottom - line.y;
      } else lines.push(new DOMRect(rect.x, rect.y, rect.width, rect.height));
    }
  }
  range.detach();
  const line = lines.find(line => x >= line.left - 5 && x <= line.right + 5 && y >= line.top - 4 && y <= line.bottom + 4);
  if (!line) return null;
  // Long prose keeps the free lens. A snapped crop would cut words at its
  // ends, while fitting the whole line recreates the oversized ribbon.
  if (line.width > 360 || line.height > 64) return null;
  return line;
}

export function mountPadding(element: HTMLElement): number {
  const requested = Number.parseFloat(element.dataset.liquidSnapPad ?? "");
  return Number.isFinite(requested) ? Math.max(4, Math.min(12, requested)) : 10;
}

export function paddedMount(element: HTMLElement, rect: DOMRect): DOMRect {
  const reading = element.dataset.liquidLens === "read";
  // Leave actual whitespace between glyphs and the original shader's rim.
  // A 34px-tall reading capsule cuts through the letterforms themselves.
  const padX = reading ? 24 : mountPadding(element);
  const padY = reading ? 16 : mountPadding(element);
  const height = Math.max(reading ? 64 : 34, rect.height + padY * 2);
  return new DOMRect(rect.left - padX, rect.top + (rect.height - height) / 2,
    Math.max(38, rect.width + padX * 2), height);
}
