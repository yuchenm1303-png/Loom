/**
 * The message that leaves the field and lands in the conversation.
 *
 * Sending used to swap two unrelated pictures: the text vanished from the field and a bubble appeared in the
 * transcript. Here the text itself makes the trip. The composer records where the text was drawn when Enter is
 * pressed (`recordSendOrigin`, before the field is cleared); the entry that the transcript then mounts for the
 * message asks for the flight (`launchSendFlight`, in its layout effect, before anything is painted). The real entry
 * is held back (`data-flight="flying"`) while two copies of it travel in a layer above the page:
 *
 *   text  the message's own content, no card. It starts exactly where the field's text was (same left edge, same line
 *         centre, a touch larger because the field is set larger than a bubble) and travels to where the bubble will
 *         be: the place it will have once the page has made room, which is known at mount (the transcript follows to
 *         its end). It only translates; the one scale it has (from the field's type size to the bubble's) is over
 *         before the text has gone far.
 *   card  the bubble's box (border, fill, shadow) with the content hidden: it comes up around the text and settles
 *         with a small spring. It carries no glyphs, so it may scale.
 *
 * Both are made with `cloneNode` of the real entry, so whatever a message holds (rich text, links, attachments) is
 * drawn as the entry will draw it, in both themes, and the hand-off at the end is the same pixels. Everything that
 * moves is a transform or opacity on the compositor: a busy main thread (the React commit that mounts the turn, the
 * thread list updating) does not make the flight stutter.
 *
 * A message from the home screen has a second part: the page it leaves (the starter cards, the activity card) is
 * copied at the moment of sending and dissolves in the layer while the message travels, instead of vanishing in one
 * frame. A message sent while a task is running (guidance) flies the same way into the live process.
 *
 * A message that will not be laid out the way the field had it (it wraps at another width, a list is drawn as a list)
 * starts as the field's own text, drawn again exactly as the field drew it, and changes into the message's layout over
 * the first moments of the flight. A starter card sets a message off from the card, its text coming in on the way.
 *
 * Nothing here is needed for correctness. Without an origin (a message sent from elsewhere), under reduced motion,
 * with the window hidden, for a message too tall to fly, or if anything is not where it should be,
 * `launchSendFlight` answers false and the entry makes its ordinary entrance.
 */
import { prefersReducedMotionNow } from "./motion/useReducedMotion";

/** Who is sending: the composer that starts a turn, or the one that steers a running turn. */
export type SendKind = "send" | "steer";

/** The home screen as it was when its message left: a copy, and where it stood. */
interface HomeSnapshot {
  copy: HTMLElement;
  rect: DOMRect;
}

/** What the field looked like, to draw its text again when the message will not be laid out the way the field had it. */
interface FieldLook {
  text: string;
  font: string;
  letterSpacing: string;
  lineHeight: number;
  color: string;
  /** Width the text wrapped to. */
  width: number;
}

export interface SendOrigin {
  kind: SendKind;
  home: HomeSnapshot | null;
  /** The field's text, to draw again at the start; null when the message did not come from a field. */
  field: FieldLook | null;
  /** The message has no text on screen to continue from (a starter card was pressed): its text comes in on the way. */
  fade: boolean;
  /** Left edge of the text in the field, and the vertical centre of the line the flight starts from (viewport px). */
  x: number;
  y: number;
  /** The field's type size, to meet the bubble's. */
  fontSize: number;
  /** How wide the text is when it fits on one line (0 when it does not): the bubble's text is scaled to this width. */
  textWidth: number;
  /** The text started below the top of a field that had scrolled: align the message's last line, not its first. */
  fromBottom: boolean;
  /** performance.now() when Enter was pressed. */
  at: number;
}

/** How long after Enter an entry may still claim the origin. */
const ORIGIN_TTL_MS = 900;
/** A message taller than this share of the transcript's viewport (or this many px) does not fly: it would cover the page for the trip. */
const MAX_FLIGHT_HEIGHT_SHARE = 0.82;
const MAX_FLIGHT_HEIGHT_PX = 520;
const FLIGHT_BASE_MS = 330;
const FLIGHT_MIN_MS = 380;
const FLIGHT_MAX_MS = 540;
const FLIGHT_MS_PER_PX = 0.18;
/** The capsule that follows the message is let in at this share of the flight: by then the message has all but arrived. */
const CAPSULE_RELEASE = 0.8;
/** The card is up when the text is this far on its way, and settles by the time it lands. */
const CARD_DELAY = 0.16;
const CARD_RISE = 0.5;
/** Within this the real entry is where the flight meant to land, and nothing needs correcting. */
const LANDING_TOLERANCE_PX = 1.5;
const LANDING_CORRECTION_MS = 110;
/** The field's own text is exchanged for the message's layout over this share of the flight. */
const FIELD_FADE = 0.22;
/** The home screen takes this long to go. */
const HOME_DISSOLVE_MS = 260;
/** `data-flight="landed"` outlives the landing by this long, so the entrance it starts can finish. */
const LANDED_MARK_MS = 900;

// Heading for the transcript. It leaves the field softly (a lift, not a shot), is fastest about a third of the way,
// and has a long, soft arrival. The two axes do not share a curve, so the path bends: the text rises first and swings
// into its place on the right (about a tenth of the way off the straight line).
const EASE_RISE = "cubic-bezier(.28, 0, .2, 1)";
const EASE_SLIDE = "cubic-bezier(.48, 0, .25, 1)";
const EASE_SETTLE = "cubic-bezier(.2, .8, .2, 1)";
/** Damped spring sampled into linear(): about 6% over, back to rest at the end. For the card only. */
const SPRING_CARD = "linear(0, .09, .3, .53, .73, .88, .98, 1.04, 1.06, 1.055, 1.04, 1.025, 1.012, 1.004, 1, 0.999, 1)";

let origin: SendOrigin | null = null;
const flights = new WeakMap<HTMLElement, Flight>();

interface Flight {
  cancel(): void;
}

/**
 * The desktop app scales through the browser's own page zoom, which leaves every measurement and every fixed position
 * in one set of pixels. The web fallback scales with CSS zoom instead, and a copy placed in the layer would not sit
 * where it was measured: there the message makes its ordinary entrance.
 */
function pageIsCssZoomed(): boolean {
  const zoom = Number.parseFloat(getComputedStyle(document.documentElement).zoom);
  return Number.isFinite(zoom) && Math.abs(zoom - 1) > 0.001;
}

/** Whether a flight may be made at all right now. */
function flightsAllowed(): boolean {
  return !prefersReducedMotionNow() && !document.hidden && !pageIsCssZoomed();
}

function emit(phase: string, detail: Record<string, unknown> = {}): void {
  window.dispatchEvent(new CustomEvent("loom:send-flight", { detail: { phase, ...detail } }));
}

/** The pane the home screen is drawn in, copied whole: the copy lays itself out like the original (it is a container too). */
function snapshotHome(): HomeSnapshot | null {
  const pane = document.querySelector<HTMLElement>(".conversation-stage > .transcript-scroll.is-empty");
  if (!pane) return null;
  const rect = pane.getBoundingClientRect();
  if (rect.width < 40 || rect.height < 40) return null;
  const copy = pane.cloneNode(true) as HTMLElement;
  copy.classList.add("send-flight-home");
  copy.removeAttribute("data-following");
  copy.setAttribute("aria-hidden", "true");
  copy.setAttribute("inert", "");
  Object.assign(copy.style, { position: "absolute", left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`, margin: "0" });
  return { copy, rect };
}

/** Where the composer's text is drawn right now. Call it when Enter is pressed, before the field is cleared. */
export function recordSendOrigin(field: HTMLTextAreaElement | null, kind: SendKind = "send"): void {
  origin = null;
  markComposerSent();
  if (!field || !flightsAllowed()) return;
  const rect = field.getBoundingClientRect();
  if (rect.width < 24 || rect.height < 12) return;
  const style = getComputedStyle(field);
  const fontSize = parseFloat(style.fontSize) || 15;
  const lineHeight = Number.parseFloat(style.lineHeight) || fontSize * 1.65;
  const paddingTop = parseFloat(style.paddingTop) || 0;
  const paddingLeft = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.borderLeftWidth) || 0);
  const scrolled = field.scrollTop > 1;
  const paddingBottom = parseFloat(style.paddingBottom) || 0;
  const contentBottom = rect.top + Math.min(rect.height, field.clientHeight) - paddingBottom;
  origin = {
    kind,
    home: kind === "send" ? snapshotHome() : null,
    fade: false,
    field: {
      text: field.value.trimEnd(),
      font: style.font,
      letterSpacing: style.letterSpacing,
      lineHeight,
      color: style.color,
      width: field.clientWidth - paddingLeft - (parseFloat(style.paddingRight) || 0),
    },
    x: rect.left + paddingLeft,
    y: scrolled ? contentBottom - lineHeight / 2 : rect.top + paddingTop + lineHeight / 2,
    fontSize,
    textWidth: fittingLineWidth(field.value.trim(), style, field.clientWidth - paddingLeft - (parseFloat(style.paddingRight) || 0)),
    fromBottom: scrolled,
    at: performance.now(),
  };
}

/**
 * A starter card on the home screen was pressed: the message starts from the card. There is no text of it on screen
 * to continue from, so the text comes in as the message sets off, while the card goes out with the rest of the page.
 */
export function recordCardOrigin(card: HTMLElement | null): void {
  origin = null;
  if (!card || !flightsAllowed()) return;
  const rect = card.getBoundingClientRect();
  if (rect.width < 24 || rect.height < 12) return;
  origin = {
    kind: "send",
    home: snapshotHome(),
    field: null,
    fade: true,
    x: rect.left + Math.min(56, rect.width * 0.2),
    y: rect.top + rect.height / 2,
    fontSize: 0,
    textWidth: 0,
    fromBottom: false,
    at: performance.now(),
  };
}

/** How wide this text is in the field's type when it fits on one line of the given width; 0 when it does not. */
function fittingLineWidth(text: string, style: CSSStyleDeclaration, available: number): number {
  if (!text || /[\r\n]/.test(text)) return 0;
  const probe = document.createElement("span");
  probe.style.cssText = "position:fixed;left:-9999px;top:0;visibility:hidden;white-space:pre;";
  probe.style.font = style.font;
  probe.style.letterSpacing = style.letterSpacing;
  probe.textContent = text;
  document.body.append(probe);
  const width = probe.getBoundingClientRect().width;
  probe.remove();
  return width <= available ? width : 0;
}

function takeOrigin(kind: SendKind): SendOrigin | null {
  const taken = origin;
  if (!taken || taken.kind !== kind) return null;
  origin = null;
  return performance.now() - taken.at <= ORIGIN_TTL_MS ? taken : null;
}

/** The home screen dissolves in the layer, under the message that is leaving it. */
function dissolveHome(layer: HTMLElement, home: HomeSnapshot): void {
  layer.prepend(home.copy);
  const done = (): void => home.copy.remove();
  home.copy.animate(
    [{ opacity: 1, translate: "0 0" }, { opacity: 0, translate: "0 -10px" }],
    { duration: HOME_DISSOLVE_MS, easing: "cubic-bezier(.4, 0, .6, 1)", fill: "forwards" },
  ).finished.then(done, done);
}

/**
 * The text just left the field: the composer that is going away (it fades while its replacement fades in) must not
 * show it a second time. Marked on the crossfade for as long as that fade lasts.
 */
let sentTimer = 0;
function markComposerSent(): void {
  const host = document.querySelector<HTMLElement>(".composer-mode-crossfade");
  if (!host) return;
  host.dataset.sent = "";
  window.clearTimeout(sentTimer);
  sentTimer = window.setTimeout(() => { delete host.dataset.sent; }, 600);
}

function ensureLayer(): HTMLElement | null {
  const shell = document.querySelector<HTMLElement>(".app-shell");
  if (!shell) return null;
  let layer = shell.querySelector<HTMLElement>(":scope > .send-flight-layer");
  if (!layer) {
    layer = document.createElement("div");
    layer.className = "send-flight-layer";
    layer.setAttribute("aria-hidden", "true");
    layer.setAttribute("inert", "");
    shell.append(layer);
  }
  return layer;
}

/** A box of the first or last glyph of the message: where its text really sits. */
function glyphBox(root: HTMLElement, which: "first" | "last"): { left: number; right: number; centre: number } | null {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => (node.nodeValue?.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
  });
  let node = walker.nextNode() as Text | null;
  if (which === "last") for (let next = node; next; next = walker.nextNode() as Text | null) node = next;
  if (!node) return null;
  const range = document.createRange();
  const text = node.nodeValue ?? "";
  if (which === "first") {
    const start = text.search(/\S/);
    range.setStart(node, Math.max(0, start));
    range.setEnd(node, Math.min(text.length, Math.max(0, start) + 1));
  } else {
    const end = text.trimEnd().length;
    range.setStart(node, Math.max(0, end - 1));
    range.setEnd(node, Math.max(1, end));
  }
  const box = range.getClientRects()[0] ?? range.getBoundingClientRect();
  if (!box || (!box.width && !box.height)) return null;
  return { left: box.left, right: box.right, centre: box.top + box.height / 2 };
}

/** The field's text as the field drew it, for the first moments of a message that will be laid out another way. */
function drawFieldText(look: FieldLook, left: number, centre: number): HTMLElement {
  const layer = document.createElement("div");
  layer.className = "send-flight-field";
  layer.textContent = look.text;
  Object.assign(layer.style, {
    position: "absolute",
    left: `${left}px`,
    top: `${centre - look.lineHeight / 2}px`,
    width: `${look.width}px`,
    margin: "0",
    font: look.font,
    letterSpacing: look.letterSpacing,
    lineHeight: `${look.lineHeight}px`,
    color: look.color,
    whiteSpace: "pre-wrap",
    overflowWrap: "break-word",
    pointerEvents: "none",
  });
  return layer;
}

/** A copy of the real entry for the layer above the page: no toolbar, no identity, still in the turn it styles itself by. */
function copyEntry(entry: HTMLElement, role: "text" | "card"): HTMLElement {
  const turn = document.createElement("section");
  turn.className = `turn-block is-active send-flight-${role}`;
  const copy = entry.cloneNode(true) as HTMLElement;
  copy.classList.remove("is-sending");
  copy.removeAttribute("data-flight");
  copy.querySelectorAll(".user-message-meta").forEach((node) => node.remove());
  for (const node of [copy, ...copy.querySelectorAll<HTMLElement>("[data-message-id], [data-loom-message-kind], [data-loom-message-text]")]) {
    node.removeAttribute("data-message-id");
    node.removeAttribute("data-loom-message-kind");
    node.removeAttribute("data-loom-message-text");
  }
  turn.append(copy);
  return turn;
}

export function launchSendFlight(entry: HTMLElement, kind: SendKind = "send"): boolean {
  if (flights.has(entry)) return true;
  if (!flightsAllowed()) return false;
  const start = takeOrigin(kind);
  if (!start) return false;
  const turn = entry.parentElement;
  const bubble = entry.querySelector<HTMLElement>(".user-message");
  const scroller = entry.closest<HTMLElement>(".transcript-scroll");
  const layer = ensureLayer();
  if (!turn || !bubble || !scroller || !layer || getComputedStyle(entry).display === "none") return false;

  // The held entry keeps its place in the layout (visibility, not display), and the capsule that will arrive under
  // it reserves its height at once: what is measured now is what the page will be when the message has landed.
  turn.dataset.flight = "flying";
  entry.dataset.flight = "flying";
  const entryBox = entry.getBoundingClientRect();
  const bubbleBox = bubble.getBoundingClientRect();
  const view = scroller.getBoundingClientRect();
  const rest = Math.max(scroller.scrollTop, scroller.scrollHeight - scroller.clientHeight);
  const shift = rest - scroller.scrollTop;
  const anchorWhich = start.fromBottom ? "last" : "first";
  const glyph = glyphBox(bubble, anchorWhich);
  const abort = (reason: string): false => {
    delete turn.dataset.flight;
    delete entry.dataset.flight;
    emit("skip", { reason, bubble: bubbleBox.height, view: view.height, stage: document.querySelector(".composer-stage")?.getBoundingClientRect().height });
    return false;
  };
  if (!glyph) return abort("no text to anchor on");
  if (bubbleBox.height > Math.min(MAX_FLIGHT_HEIGHT_PX, view.height * MAX_FLIGHT_HEIGHT_SHARE)) return abort("message too tall to fly");
  if (entryBox.width < 40) return abort("entry has no width");

  const finalTop = entryBox.top - shift;
  const glyphCentre = glyph.centre - shift;
  const dx = start.x - glyph.left;
  const dy = start.y - glyphCentre;
  const distance = Math.hypot(dx, dy);
  if (distance < 24) return abort("already in place");
  const duration = Math.round(Math.min(FLIGHT_MAX_MS, Math.max(FLIGHT_MIN_MS, FLIGHT_BASE_MS + distance * FLIGHT_MS_PER_PX)));
  // Meet the field's text: by width when both are one line (so the line ends where the field's did), else by type size.
  const bubbleFont = parseFloat(getComputedStyle(bubble).fontSize) || 14;
  const lastGlyph = start.textWidth && glyphBox(bubble, "last");
  const oneLine = lastGlyph && bubbleBox.height < bubbleFont * 3.2 && lastGlyph.right > glyph.left;
  const ratio = start.fontSize ? Math.min(1.12, Math.max(0.92, oneLine ? start.textWidth / (lastGlyph.right - glyph.left) : start.fontSize / bubbleFont)) : 1;

  const ghost = document.createElement("div");
  ghost.className = "send-flight-ghost";
  ghost.style.left = `${entryBox.left}px`;
  ghost.style.top = `${finalTop}px`;
  ghost.style.width = `${entryBox.width}px`;
  const text = copyEntry(entry, "text");
  const card = copyEntry(entry, "card");
  // The scale that meets the field's type size turns about the first glyph, so that glyph stays where it was.
  text.style.transformOrigin = `${glyph.left - entryBox.left}px ${glyph.centre - entryBox.top}px`;
  card.style.transformOrigin = `${bubbleBox.left + bubbleBox.width / 2 - entryBox.left}px ${bubbleBox.top + bubbleBox.height / 2 - entryBox.top}px`;
  ghost.append(text, card);
  // A message that will not be laid out the way the field had it (it wraps at another width, a list is drawn as a
  // list) starts as the field's own text, drawn again exactly as the field drew it, and changes into the message's
  // layout while it is on its way: the first frame is the field, with no jump in the middle of a paragraph.
  const fieldLayer = start.field && !oneLine && !start.fromBottom && start.field.text ? drawFieldText(start.field, glyph.left - entryBox.left, glyph.centre - entryBox.top) : null;
  if (fieldLayer) ghost.append(fieldLayer);
  if (start.home) dissolveHome(layer, start.home);
  layer.append(ghost);

  const options = (easing: string, extra: KeyframeAnimationOptions = {}): KeyframeAnimationOptions => ({ duration, easing, fill: "backwards", ...extra });
  const animations = [
    ghost.animate([{ translate: `${dx}px 0px` }, { translate: "0px 0px" }], options(EASE_SLIDE)),
    ghost.animate([{ transform: `translateY(${dy}px)` }, { transform: "translateY(0px)" }], options(EASE_RISE)),
    ...(Math.abs(ratio - 1) > 0.004
      ? [text.animate([{ scale: String(ratio) }, { scale: "1" }], options(EASE_SETTLE, { duration: Math.round(duration * 0.42) }))]
      : []),
    ...(start.fade ? [text.animate([{ opacity: 0 }, { opacity: 1 }], options("ease-out", { duration: duration * FIELD_FADE * 1.4, fill: "both" }))] : []),
    ...(fieldLayer ? [
      fieldLayer.animate([{ opacity: 1 }, { opacity: 0 }], options("ease-in", { duration: duration * FIELD_FADE, fill: "both" })),
      text.animate([{ opacity: 0 }, { opacity: 1 }], options("ease-out", { duration: duration * FIELD_FADE, fill: "both" })),
    ] : []),
    card.animate([{ opacity: 0 }, { opacity: 1 }], options("cubic-bezier(.3, .6, .3, 1)", { delay: duration * CARD_DELAY, duration: duration * CARD_RISE, fill: "both" })),
    card.animate([{ scale: "0.9" }, { scale: "1" }], options(SPRING_CARD, { delay: duration * CARD_DELAY, duration: duration * (1 - CARD_DELAY), fill: "both" })),
  ];
  const release = Math.round(duration * CAPSULE_RELEASE);
  turn.style.setProperty("--flight-lead", `${Math.max(0, release - 70)}ms`);
  const releaseTimer = window.setTimeout(() => { if (turn.dataset.flight === "flying") turn.dataset.flight = "settling"; }, release);
  emit("launch", { duration, dx, dy, ratio, pageHeight: scroller.scrollHeight });

  let finished = false;
  let correction: Animation | null = null;
  let tracker = 0;
  // Where the message is aimed. It was aimed at the page as it was at mount; a page that is still changing (a run
  // streaming above or below, a capsule growing, a composer of another height) moves the place the message will
  // rest in. The flight follows that place a little more closely as it goes, so it arrives where the entry is
  // instead of where the entry was going to be. Only the ghost's own box moves for this; its animations are untouched.
  const startedAt = performance.now();
  const aim = { x: 0, y: 0 };
  let lastTrack = startedAt;
  const track = (now: number): void => {
    if (finished) return;
    if (entry.isConnected) {
      const real = entry.getBoundingClientRect();
      const settledAt = Math.max(scroller.scrollTop, scroller.scrollHeight - scroller.clientHeight);
      const restTop = real.top - (settledAt - scroller.scrollTop);
      const progress = Math.min(1, (now - startedAt) / duration);
      const late = Math.max(0, Math.min(1, (progress - 0.55) / 0.45));
      const blend = late * late * (3 - 2 * late);
      const wantX = (real.left - entryBox.left) * progress;
      const wantY = (restTop + (real.top - restTop) * blend - finalTop) * progress;
      // Low-pass, so that a sudden change of layout is walked into instead of jumped.
      const k = 1 - Math.exp(-(now - lastTrack) / 55);
      aim.x += (wantX - aim.x) * k;
      aim.y += (wantY - aim.y) * k;
      if (Math.abs(aim.x) > 0.02 || Math.abs(aim.y) > 0.02) {
        ghost.style.left = `${entryBox.left + aim.x}px`;
        ghost.style.top = `${finalTop + aim.y}px`;
      }
    }
    lastTrack = now;
    tracker = requestAnimationFrame(track);
  };
  tracker = requestAnimationFrame(track);
  const reveal = (): void => {
    if (finished) return;
    finished = true;
    cancelAnimationFrame(tracker);
    window.clearTimeout(guard);
    window.clearTimeout(releaseTimer);
    correction?.cancel();
    ghost.remove();
    if (entry.isConnected) {
      entry.dataset.flight = "landed";
      turn.dataset.flight = "landed";
      window.setTimeout(() => {
        delete entry.dataset.flight;
        delete turn.dataset.flight;
        turn.style.removeProperty("--flight-lead");
      }, LANDED_MARK_MS);
    }
    flights.delete(entry);
    emit("land");
  };
  const land = (): void => {
    if (finished) return;
    if (!entry.isConnected || document.hidden) { reveal(); return; }
    // Where the entry really is now. The page was predicted at mount; if anything moved it since (a composer of
    // another height, a font arriving) the last stretch closes the difference before the hand-off.
    cancelAnimationFrame(tracker);
    const real = entry.getBoundingClientRect();
    const missX = real.left - (entryBox.left + aim.x);
    const missY = real.top - (finalTop + aim.y);
    if (Math.abs(missX) <= LANDING_TOLERANCE_PX && Math.abs(missY) <= LANDING_TOLERANCE_PX) {
      ghost.style.left = `${real.left}px`;
      ghost.style.top = `${real.top}px`;
      reveal();
      return;
    }
    emit("correct", { missX, missY });
    correction = ghost.animate(
      [{ translate: "0px 0px" }, { translate: `${missX}px ${missY}px` }],
      { duration: LANDING_CORRECTION_MS, easing: EASE_SETTLE, fill: "forwards" },
    );
    correction.finished.then(reveal, reveal);
  };
  const guard = window.setTimeout(land, duration + 260);
  Promise.all(animations.map((animation) => animation.finished)).then(land, reveal);

  flights.set(entry, {
    cancel: () => {
      for (const animation of animations) animation.cancel();
      reveal();
    },
  });
  // StrictMode (development) mounts, unmounts and mounts an effect again; the node survives that. A node that
  // is really gone takes its flight with it.
  return true;
}

/** An entry that unmounts ends its flight. Deferred a microtask: a remount of the same node is not an unmount. */
export function endSendFlight(entry: HTMLElement | null): void {
  if (!entry) return;
  queueMicrotask(() => {
    if (!entry.isConnected) flights.get(entry)?.cancel();
  });
}
