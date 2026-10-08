import "./custom-scrollbars.css";

type Bar = { target: HTMLElement; axis: "x" | "y"; track: HTMLDivElement; thumb: HTMLDivElement; base?: { cross: number; start: number; end: number } };
const entries = new Map<HTMLElement, Bar[]>();
const observedChildren = new Map<HTMLElement, Element>();
const dirty = new Set<HTMLElement>();
const added = new Set<Element>();
const restyled = new Set<HTMLElement>();
let frame = 0;
let host: HTMLDivElement;
const candidates = "div,main,section,aside,pre,textarea,ul";

function schedule(target?: HTMLElement) {
  if (target) dirty.add(target);
  if (!frame) frame = requestAnimationFrame(flush);
}

function update(bar: Bar) {
  const el = bar.target;
  const rect = el.getBoundingClientRect();
  const vertical = bar.axis === "y";
  const viewport = vertical ? el.clientHeight : el.clientWidth;
  const extent = vertical ? el.scrollHeight : el.scrollWidth;
  const position = vertical ? el.scrollTop : el.scrollLeft;
  const length = vertical ? rect.height : rect.width;
  const overflow = getComputedStyle(el);
  // A collapsed or offscreen panel must not leave an interactive floating bar.
  const visible = /^(auto|scroll)$/.test(vertical ? overflow.overflowY : overflow.overflowX)
    && rect.width > 0 && rect.height > 0 && extent > viewport + 1
    && rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth
    && el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  bar.track.hidden = !visible;
  if (!visible) return;
  // Clip to enclosing viewports (nested code blocks, sidebar folders, dialogs).
  let top = Math.max(0, rect.top), bottom = Math.min(innerHeight, rect.bottom);
  let left = Math.max(0, rect.left), right = Math.min(innerWidth, rect.right);
  for (let parent = el.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
    const style = getComputedStyle(parent);
    if (/(auto|scroll|hidden|clip)/.test(style.overflowY + style.overflowX)) {
      const bounds = parent.getBoundingClientRect();
      top = Math.max(top, bounds.top); bottom = Math.min(bottom, bounds.bottom);
      left = Math.max(left, bounds.left); right = Math.min(right, bounds.right);
    }
  }
  if (bottom <= top || right <= left) { bar.track.hidden = true; return; }
  const hitX = vertical ? rect.right - 6 : (left + right) / 2;
  const hitY = vertical ? (top + bottom) / 2 : rect.bottom - 6;
  const hit = document.elementsFromPoint(hitX, hitY).find((node) => !host.contains(node));
  if (hit && !el.contains(hit) && !hit.contains(el)) { bar.track.hidden = true; return; }
  const otherAxisScrolls = vertical ? el.scrollWidth > el.clientWidth + 1 : el.scrollHeight > el.clientHeight + 1;
  const trackLength = Math.max(1, length - 4 - (otherAxisScrolls ? 10 : 0));
  bar.base = vertical
    ? { cross: rect.right - 10, start: top, end: Math.min(bottom, rect.top + 2 + trackLength) }
    : { cross: rect.bottom - 10, start: left, end: Math.min(right, rect.left + 2 + trackLength) };
  const size = Math.min(trackLength, Math.max(24, trackLength * viewport / extent));
  const offset = (trackLength - size) * Math.max(0, position) / Math.max(1, extent - viewport);
  Object.assign(bar.track.style, vertical
    ? { left: `${rect.right - 10}px`, top: `${rect.top + 2}px`, width: "8px", height: `${trackLength}px`, clipPath: `inset(${Math.max(0, top - rect.top)}px 0 ${Math.max(0, rect.bottom - bottom)}px 0)` }
    : { left: `${rect.left + 2}px`, top: `${rect.bottom - 10}px`, width: `${trackLength}px`, height: "8px", clipPath: `inset(0 ${Math.max(0, rect.right - right)}px 0 ${Math.max(0, left - rect.left)}px)` });
  Object.assign(bar.thumb.style, vertical
    ? { height: `${size}px`, width: "100%", transform: `translateY(${offset}px)` }
    : { width: `${size}px`, height: "100%", transform: `translateX(${offset}px)` });
  bar.thumb.setAttribute("aria-valuemax", String(Math.round(extent - viewport)));
  bar.thumb.setAttribute("aria-valuenow", String(Math.round(position)));
}

const resize = new ResizeObserver((events) => {
  for (const event of events) if (event.target instanceof HTMLElement) markAncestors(event.target);
});

function markAncestors(node: Element) {
  for (let el: Element | null = node; el; el = el.parentElement) {
    if (entries.has(el as HTMLElement)) schedule(el as HTMLElement);
  }
}

function register(el: HTMLElement) {
  if (entries.has(el) || host.contains(el)) return;
  const style = getComputedStyle(el);
  const axes = (["x", "y"] as const).filter((axis) => /^(auto|scroll)$/.test(axis === "x" ? style.overflowX : style.overflowY));
  if (!axes.length) return;
  const bars = axes.map((axis): Bar => {
    const track = document.createElement("div"), thumb = document.createElement("div");
    track.className = `loom-scroll-track loom-scroll-${axis}`;
    track.hidden = true;
    thumb.className = "loom-scroll-thumb";
    thumb.tabIndex = 0;
    thumb.setAttribute("role", "scrollbar");
    thumb.setAttribute("aria-label", axis === "y" ? "垂直滚动" : "水平滚动");
    thumb.setAttribute("aria-orientation", axis === "y" ? "vertical" : "horizontal");
    thumb.setAttribute("aria-valuemin", "0");
    const vertical = axis === "y";
    const get = () => vertical ? el.scrollTop : el.scrollLeft;
    const set = (value: number) => { if (vertical) el.scrollTop = value; else el.scrollLeft = value; schedule(el); };
    let drag: { point: number; scroll: number; ratio: number } | null = null;
    thumb.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault(); event.stopPropagation();
      thumb.setPointerCapture(event.pointerId);
      // Notify the transcript policy that this is human scroll intent.
      el.dispatchEvent(new CustomEvent("loom:scrollbar-drag-start", { bubbles: true }));
      const viewport = vertical ? el.clientHeight : el.clientWidth;
      const extent = vertical ? el.scrollHeight : el.scrollWidth;
      const length = vertical ? track.clientHeight - thumb.offsetHeight : track.clientWidth - thumb.offsetWidth;
      drag = { point: vertical ? event.clientY : event.clientX, scroll: get(), ratio: (extent - viewport) / Math.max(1, length) };
    });
    thumb.addEventListener("pointermove", (event) => { if (drag) set(drag.scroll + ((vertical ? event.clientY : event.clientX) - drag.point) * drag.ratio); });
    const finish = () => { drag = null; el.dispatchEvent(new CustomEvent("loom:scrollbar-drag-end", { bubbles: true })); };
    thumb.addEventListener("pointerup", finish);
    thumb.addEventListener("pointercancel", finish);
    thumb.addEventListener("lostpointercapture", finish);
    track.addEventListener("pointerdown", (event) => {
      if (event.target !== track || event.button !== 0) return;
      el.dispatchEvent(new CustomEvent("loom:scrollbar-drag-start", { bubbles: true }));
      const rect = thumb.getBoundingClientRect();
      set(get() + ((vertical ? event.clientY < rect.top : event.clientX < rect.left) ? -1 : 1) * (vertical ? el.clientHeight : el.clientWidth));
      el.dispatchEvent(new CustomEvent("loom:scrollbar-drag-end", { bubbles: true }));
    });
    thumb.addEventListener("keydown", (event) => {
      const step = vertical ? el.clientHeight : el.clientWidth;
      const values: Record<string, number> = { ArrowUp: get() - 40, ArrowLeft: get() - 40, ArrowDown: get() + 40, ArrowRight: get() + 40, PageUp: get() - step, PageDown: get() + step, Home: 0, End: vertical ? el.scrollHeight : el.scrollWidth };
      if (!(event.key in values)) return;
      event.preventDefault(); event.stopPropagation();
      el.dispatchEvent(new CustomEvent("loom:scrollbar-drag-start", { bubbles: true }));
      set(values[event.key]);
      el.dispatchEvent(new CustomEvent("loom:scrollbar-drag-end", { bubbles: true }));
    });
    track.addEventListener("wheel", (event) => {
      event.preventDefault();
      el.dispatchEvent(new WheelEvent("wheel", { deltaY: event.deltaY, deltaX: event.deltaX, bubbles: true }));
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? (vertical ? el.clientHeight : el.clientWidth) : 1;
      set(get() + (vertical ? event.deltaY : event.deltaX || event.deltaY) * unit);
    }, { passive: false });
    track.append(thumb); host.append(track);
    return { target: el, axis, track, thumb };
  });
  entries.set(el, bars);
  el.classList.add("loom-custom-scrollable");
  resize.observe(el);
  if (el.firstElementChild) { resize.observe(el.firstElementChild); observedChildren.set(el, el.firstElementChild); }
  schedule(el);
}

function discover(node: Element) {
  if (host.contains(node)) return;
  if (node instanceof HTMLElement && node.matches(candidates)) register(node);
  node.querySelectorAll<HTMLElement>(candidates).forEach(register);
}

function flush() {
  frame = 0;
  // Mutation batches often contain both a parent and its newly mounted children.
  // Walk each subtree once, rather than repeating discovery for nested records.
  for (const node of added) {
    if (!node.isConnected) continue;
    let covered = false;
    for (let parent = node.parentElement; parent; parent = parent.parentElement) {
      if (added.has(parent)) { covered = true; break; }
    }
    if (!covered) discover(node);
  }
  added.clear();
  for (const target of restyled) if (target.isConnected) register(target);
  restyled.clear();
  for (const [target, bars] of entries) if (!target.isConnected) {
    bars.forEach((bar) => bar.track.remove()); resize.unobserve(target);
    const child = observedChildren.get(target);
    if (child) resize.unobserve(child);
    observedChildren.delete(target); entries.delete(target); dirty.delete(target);
  }
  for (const target of dirty) {
    const previous = observedChildren.get(target);
    if (previous !== target.firstElementChild) {
      if (previous) resize.unobserve(previous);
      observedChildren.delete(target);
      if (target.firstElementChild) { resize.observe(target.firstElementChild); observedChildren.set(target, target.firstElementChild); }
    }
    entries.get(target)?.forEach(update);
  }
  dirty.clear();
  // Nested scrollports can share a right/bottom edge. Keep both controls, but
  // allocate distinct lanes instead of painting two thumbs on top of each other.
  const placed: { axis: "x" | "y"; cross: number; start: number; end: number }[] = [];
  for (const bars of entries.values()) for (const bar of bars) {
    if (bar.track.hidden || !bar.base) continue;
    let cross = bar.base.cross;
    while (placed.some((other) => other.axis === bar.axis && Math.abs(other.cross - cross) < 10
      && other.start < bar.base!.end && bar.base!.start < other.end)) cross -= 10;
    bar.track.style[bar.axis === "y" ? "left" : "top"] = `${cross}px`;
    placed.push({ axis: bar.axis, cross, start: bar.base.start, end: bar.base.end });
  }
}

function onScroll(event: Event) {
  // Parent scrolling also repositions nested viewport overlays.
  const target = event.target;
  if (!(target instanceof Element)) return;
  for (const el of entries.keys()) if (el === target || target.contains(el)) schedule(el);
}
function updateAll() { for (const el of entries.keys()) schedule(el); }
const mutation = new MutationObserver((records) => {
  for (const record of records) {
    const target = record.target instanceof Element ? record.target : record.target.parentElement;
    if (!target || host.contains(target)) continue;
    markAncestors(target);
    if (record.type === "attributes") {
      if (record.attributeName === "class" || inheritedStyleChanged(record.oldValue, target.getAttribute("style"))) {
        added.add(target);
      } else if (target instanceof HTMLElement && target.matches(candidates)) {
        // A transform/height/opacity animation cannot change descendants' CSS
        // overflow rules. Only inspect this element for new inline overflow.
        restyled.add(target);
      }
      schedule();
      for (const el of entries.keys()) if (target.contains(el)) schedule(el);
    }
    for (const node of record.addedNodes) if (node instanceof Element) {
      added.add(node); schedule();
      if (node.matches('[role="dialog"], [role="menu"], .settings-host, .global-context-menu')) updateAll();
    }
    if (record.removedNodes.length) {
      schedule();
      if (Array.from(record.removedNodes).some((node) => node instanceof Element
        && node.matches('[role="dialog"], [role="menu"], .settings-host, .global-context-menu'))) updateAll();
    }
  }
});
function inheritedStyleChanged(previous: string | null, next: string | null): boolean {
  // Custom properties and `all` can alter descendant overflow declarations.
  const inherited = (value: string | null) => (value?.match(/(?:^|;)\s*(?:--[^:;]+|all)\s*:[^;]*/g) ?? []).join(";");
  return inherited(previous) !== inherited(next);
}
host = document.createElement("div");
host.className = "loom-scrollbar-layer";
document.body.append(host);
discover(document.body);
mutation.observe(document.body, { subtree: true, childList: true, attributes: true, attributeOldValue: true, attributeFilter: ["class", "style"], characterData: true });
document.addEventListener("scroll", onScroll, true);
const onInput = (event: Event) => { if (event.target instanceof Element) markAncestors(event.target); };
document.addEventListener("input", onInput, true);
window.addEventListener("resize", updateAll);
document.addEventListener("transitionend", updateAll);
if (import.meta.hot) import.meta.hot.dispose(() => {
  mutation.disconnect(); resize.disconnect(); cancelAnimationFrame(frame);
  entries.forEach((_bars, target) => target.classList.remove("loom-custom-scrollable"));
  host.remove(); document.removeEventListener("scroll", onScroll, true);
  document.removeEventListener("input", onInput, true);
  window.removeEventListener("resize", updateAll); document.removeEventListener("transitionend", updateAll);
});
