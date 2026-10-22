import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { ChevronLeft } from "./icons";
import { useMotionPresence, type MotionPresencePhase } from "../motion/useMotionPresence";
import { RETAIN } from "../motion/tokens";
import "./composer-panels.css";

/**
 * The composer's panels: tools & skills, access, model, usage and the draft preview. They are one family
 * (composer-panels.css owns every rule they wear, docs/composer-panels-2026-10-22.md says why), and they behave as
 * one: a host per composer knows which panel is open, so opening one closes the other, a press outside the open
 * panel or Escape closes it (Escape and a choice hand the focus back to its trigger), and the composer shows that a
 * panel is up while it is mounted, its exit included.
 */

export type ComposerPanelId = "tools" | "permission" | "model" | "usage" | "preview";

export type ComposerPanelHostState = Host;

interface Host {
  openId: ComposerPanelId | null;
  /** Panels still on screen, the one leaving included. */
  mounted: boolean;
  open(id: ComposerPanelId, anchor: RefObject<HTMLElement | null>, trigger: RefObject<HTMLElement | null>): void;
  close(id?: ComposerPanelId, restoreFocus?: boolean): void;
  report(id: ComposerPanelId, mounted: boolean): void;
}

const HostContext = createContext<Host | null>(null);

/** One per composer surface. Returns the host to provide; `mounted` drives the composer's `has-panel`. */
export function useComposerPanelHost(rootRef: RefObject<HTMLElement | null>): Host {
  const [openId, setOpenId] = useState<ComposerPanelId | null>(null);
  const [mountedIds, setMountedIds] = useState<ReadonlySet<ComposerPanelId>>(() => new Set());
  const anchorRef = useRef<RefObject<HTMLElement | null> | null>(null);
  const triggerRef = useRef<RefObject<HTMLElement | null> | null>(null);
  const openRef = useRef(openId);
  openRef.current = openId;

  const close = useCallback((id?: ComposerPanelId, restoreFocus = false) => {
    if (id && openRef.current !== id) return;
    if (restoreFocus) triggerRef.current?.current?.focus({ preventScroll: true });
    setOpenId(null);
  }, []);

  const open = useCallback((id: ComposerPanelId, anchor: RefObject<HTMLElement | null>, trigger: RefObject<HTMLElement | null>) => {
    anchorRef.current = anchor;
    triggerRef.current = trigger;
    setOpenId(id);
  }, []);

  const report = useCallback((id: ComposerPanelId, mounted: boolean) => {
    setMountedIds((current) => {
      if (current.has(id) === mounted) return current;
      const next = new Set(current);
      if (mounted) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  useEffect(() => {
    if (!openId) return;
    function onPointerDown(event: PointerEvent) {
      if (anchorRef.current?.current?.contains(event.target as Node)) return;
      setOpenId(null);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      event.preventDefault();
      triggerRef.current?.current?.focus({ preventScroll: true });
      setOpenId(null);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [openId]);

  // The transcript's jump button steps aside while a panel is up. An attribute on the workspace, because
  // `.workspace:has(.composer-popover)` was re-evaluated on every change in the transcript.
  const mounted = mountedIds.size > 0;
  useLayoutEffect(() => {
    if (!mounted) return;
    const workspace = rootRef.current?.closest(".workspace");
    if (!workspace) return;
    workspace.setAttribute("data-composer-popover", "");
    return () => workspace.removeAttribute("data-composer-popover");
  }, [mounted, rootRef]);

  return useMemo(() => ({ openId, mounted, open, close, report }), [openId, mounted, open, close, report]);
}

export function ComposerPanelHost({ host, children }: { host: Host; children: ReactNode }) {
  return <HostContext.Provider value={host}>{children}</HostContext.Provider>;
}

/** The surrounding composer's host, if there is one (the live-guidance composer lends its own to the shared controls). */
export function useInheritedComposerPanelHost(): Host | null {
  return useContext(HostContext);
}

export interface ComposerPanelState {
  id: ComposerPanelId;
  open: boolean;
  phase: MotionPresencePhase;
  mounted: boolean;
  /** Holds the trigger and the panel: a press inside it does not close the panel. */
  anchorRef: RefObject<HTMLDivElement | null>;
  triggerRef: RefObject<HTMLButtonElement | null>;
  toggle(): void;
  show(): void;
  close(restoreFocus?: boolean): void;
}

const fallbackRoot = { current: null };

/**
 * One panel's state. It belongs to the host it is given, else to the composer's host around it, else (a panel rendered
 * on its own, as in a fixture) to a host of its own.
 */
export function useComposerPanel(id: ComposerPanelId, explicitHost?: ComposerPanelHostState | null): ComposerPanelState {
  const inherited = useContext(HostContext);
  const own = useComposerPanelHost(fallbackRoot);
  const host = explicitHost ?? inherited ?? own;
  const { open: openPanel, close: closePanel, report } = host;
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const open = host.openId === id;
  const presence = useMotionPresence(open, RETAIN.fast);
  useEffect(() => {
    report(id, presence.mounted);
  }, [id, presence.mounted, report]);
  useEffect(() => () => report(id, false), [id, report]);

  const show = useCallback(() => openPanel(id, anchorRef, triggerRef), [openPanel, id]);
  const close = useCallback((restoreFocus = false) => closePanel(id, restoreFocus), [closePanel, id]);
  const toggle = useCallback(
    () => (open ? closePanel(id) : openPanel(id, anchorRef, triggerRef)),
    [openPanel, closePanel, id, open],
  );
  return { id, open, phase: presence.phase, mounted: presence.mounted, anchorRef, triggerRef, toggle, show, close };
}

const ITEM = "[data-cp-item]:not(:disabled):not([aria-disabled='true'])";

function items(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(ITEM)).filter((item) => item.offsetParent !== null);
}

/** Up and down walk a panel's rows; up from the first row returns to its search field. */
function walk(event: ReactKeyboardEvent<HTMLElement>) {
  if (event.defaultPrevented || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
  const root = event.currentTarget;
  const target = event.target as HTMLElement;
  const typing = target instanceof HTMLInputElement && target.type !== "checkbox";
  if (typing && (event.key === "Home" || event.key === "End")) return;
  const list = items(root);
  if (!list.length) return;
  event.preventDefault();
  const index = list.indexOf(document.activeElement as HTMLElement);
  let next: HTMLElement | undefined;
  if (event.key === "Home") next = list[0];
  else if (event.key === "End") next = list[list.length - 1];
  else if (event.key === "ArrowDown") next = list[Math.min(list.length - 1, index + 1)];
  else if (index > 0) next = list[index - 1];
  else next = root.querySelector<HTMLElement>(".cp-search input") ?? list[0];
  next?.focus({ preventScroll: true });
  next?.scrollIntoView({ block: "nearest" });
}

export type ComposerPanelSize = "s" | "m" | "l";
const WIDTH: Record<ComposerPanelSize, number> = { s: 320, m: 360, l: 400 };

/**
 * The shell every composer panel wears. It hangs above its trigger, starts at its trigger's left edge (or right edge,
 * `align="end"`) and is nudged to stay on screen; it is as tall as its content up to the room above the trigger, and
 * its body scrolls beyond that. It comes and goes like every popover (readable on its first frame, see
 * composer-panels.css), and while it leaves it no longer takes input.
 */
export function ComposerPanel({
  panel,
  label,
  role = "dialog",
  size = "m",
  align = "start",
  className = "",
  children,
}: {
  panel: ComposerPanelState;
  label: string;
  role?: "dialog" | "menu";
  size?: ComposerPanelSize;
  align?: "start" | "end";
  /** Hook classes the tests and older selectors find the panel by; they carry no style. */
  className?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement | null>(null);
  const keyboardOpen = useRef(false);

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element || !panel.mounted) return;
    function place() {
      const node = ref.current;
      const anchor = node?.offsetParent as HTMLElement | null;
      if (!node || !anchor) return;
      const margin = 12;
      const box = anchor.getBoundingClientRect();
      const width = node.offsetWidth;
      const viewport = document.documentElement.clientWidth;
      let x = align === "end" ? box.width - width : 0;
      const left = box.left + x;
      if (left + width > viewport - margin) x -= left + width - (viewport - margin);
      if (box.left + x < margin) x += margin - (box.left + x);
      node.style.setProperty("--cp-x", `${Math.round(x)}px`);
      node.style.setProperty("--cp-room", `${Math.max(180, Math.round(box.top - 10 - margin))}px`);
    }
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [align, panel.mounted]);

  // A panel opened from the keyboard takes the focus to its current row (or its search field, which focuses itself).
  useEffect(() => {
    if (!panel.open || !keyboardOpen.current) return;
    const element = ref.current;
    if (!element || element.contains(document.activeElement)) return;
    const target = element.querySelector<HTMLElement>("[data-cp-current]") ?? items(element)[0];
    target?.focus({ preventScroll: true });
  }, [panel.open]);

  useEffect(() => {
    const trigger = panel.triggerRef.current;
    if (!trigger) return;
    // `detail` is 0 for a click the keyboard made (Enter or Space).
    const remember = (event: MouseEvent) => { keyboardOpen.current = event.detail === 0; };
    trigger.addEventListener("click", remember, true);
    return () => trigger.removeEventListener("click", remember, true);
  }, [panel.triggerRef, panel.mounted]);

  if (!panel.mounted) return null;
  return (
    <section
      ref={ref}
      className={["cp", ...className.split(/\s+/), "composer-popover"].filter(Boolean).join(" ")}
      data-composer-panel={panel.id}
      data-motion-phase={panel.phase}
      data-align={align}
      inert={!panel.open}
      role={role}
      aria-label={label}
      style={{ "--cp-width": `${WIDTH[size]}px` } as CSSProperties}
      onKeyDown={walk}
    >
      {children}
    </section>
  );
}

/** The first row of a panel: a title (and a line under it), what belongs at its end, and a way back if it has one. */
export function PanelHead({
  title,
  subtitle,
  lead,
  aside,
  onBack,
  backLabel,
  className = "",
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  lead?: ReactNode;
  aside?: ReactNode;
  onBack?(): void;
  backLabel?: string;
  className?: string;
}) {
  return (
    <header className={`cp-head ${onBack ? "has-back" : ""} composer-popover-head ${className}`.trim()}>
      {onBack ? (
        <button type="button" className="cp-icon" onClick={onBack} aria-label={backLabel} title={backLabel}>
          <ChevronLeft size={16} strokeWidth={1.75} />
        </button>
      ) : null}
      {lead}
      <div className="cp-head-text">
        <div className="cp-title">{title}</div>
        {subtitle ? <div className="cp-subtitle">{subtitle}</div> : null}
      </div>
      {aside ? <div className="cp-aside">{aside}</div> : null}
    </header>
  );
}

/** A section's name inside a panel body, with an optional action at its end. */
export function PanelLabel({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="cp-label">
      <span>{children}</span>
      {action}
    </div>
  );
}

/** The quiet line under a panel: what a choice does, or the panel's own actions. */
export function PanelFoot({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <footer className={`cp-foot ${className}`.trim()}>{children}</footer>;
}

/**
 * Two to four choices that share one place, with one slab that travels to the chosen one. The slab is a mark, so it
 * moves (the settle spring on the flow step); the words stay where they are and only change ink.
 */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  role = "tablist",
  disabled,
  className = "",
}: {
  value: T | null;
  options: Array<{ value: T; label: ReactNode; title?: string; disabled?: boolean }>;
  onChange(value: T): void;
  label: string;
  role?: "tablist" | "radiogroup";
  disabled?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);
  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    function measure() {
      const node = ref.current;
      const active = node?.querySelector<HTMLElement>("[data-active='true']");
      if (!node) return;
      node.style.setProperty("--cp-slab-x", `${active ? active.offsetLeft : 0}px`);
      node.style.setProperty("--cp-slab-w", `${active ? active.offsetWidth : 0}px`);
      node.dataset.slab = active ? "on" : "off";
    }
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    // The slab lands in place on the first frame and travels from then on.
    const frame = requestAnimationFrame(() => setReady(true));
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [value, options.length]);
  return (
    <div ref={ref} className={`cp-seg ${className}`.trim()} role={role} aria-label={label} data-ready={ready || undefined}>
      <span className="cp-seg-slab" aria-hidden="true" />
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role={role === "tablist" ? "tab" : "radio"}
            aria-selected={role === "tablist" ? active : undefined}
            aria-checked={role === "radiogroup" ? active : undefined}
            data-active={active}
            title={option.title}
            disabled={disabled || option.disabled}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
