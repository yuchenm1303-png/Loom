import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { LoomPetArt, type PetPose } from "./LoomPetArt";
import "./loom-pet.css";

export type LoomPetState = "idle" | "listening" | "working" | "approval" | "sleeping";
type Props = { running: boolean; approval: boolean; completed: boolean };
type Reaction = "hello" | "petting" | "tickle" | "hop" | "delight" | "wake";
type Zone = "head" | "body" | "tail";
const REACTION_MS: Record<Reaction, number> = { hello: 700, petting: 1400, tickle: 1000, hop: 900, delight: 1500, wake: 900 };

/** React owns this pet alongside the composer. No page-wide observers or extra roots. */
export function LoomPet({ running, approval, completed }: Props) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [focused, setFocused] = useState(false);
  const [sleeping, setSleeping] = useState(false);
  const [blinking, setBlinking] = useState(false);
  const [reaction, setReaction] = useState<{ kind: Reaction; id: number } | null>(null);
  const [nearby, setNearby] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [typing, setTyping] = useState(false);
  const [lookY, setLookY] = useState(0);
  const [anchor, setAnchor] = useState<{ left: number; top: number; min: number; max: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [celebrating, setCelebrating] = useState(false);
  const [look, setLook] = useState(0);
  const [pose, setPose] = useState<PetPose>(0);
  const [ear, setEar] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const previousRunning = useRef(running);
  const serial = useRef(0);
  const lastHello = useRef(-Infinity);
  const lastStroke = useRef(-Infinity);
  const stroke = useRef({ x: 0, y: 0, distance: 0 });
  const zone = useRef<Zone>("body");
  const lastClick = useRef({ at: -Infinity, count: 0 });
  const wakingClick = useRef(false);
  const perch = useRef(1);
  const drag = useRef<{ pointer: number; x: number; left: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const interactive = !running && !approval;
  const react = (kind: Reaction) => setReaction({ kind, id: ++serial.current });
  const state: LoomPetState = approval ? "approval" : running ? "working"
    : focused ? "listening" : sleeping ? "sleeping" : "idle";

  useLayoutEffect(() => {
    const stage = buttonRef.current?.closest<HTMLElement>(".composer-stage");
    const composer = stage?.querySelector<HTMLElement>(".composer");
    if (!stage || !composer) return;
    // Both rectangles share ancestor transforms. Convert back to local CSS pixels,
    // including renderer zoom; padding/hints no longer change the landing point.
    const sync = () => {
      const stageBox = stage.getBoundingClientRect();
      const box = composer.getBoundingClientRect();
      if (!stage.offsetWidth || !stage.offsetHeight || !box.width) return;
      const sx = stageBox.width / stage.offsetWidth;
      const sy = stageBox.height / stage.offsetHeight;
      const min = Math.ceil((box.left - stageBox.left) / sx + Math.min(24, box.width / sx / 4));
      const max = Math.max(min, Math.floor((box.right - stageBox.left) / sx - 64 - 24));
      const next = {
        left: Math.round(min + (max - min) * perch.current),
        top: Math.floor((box.top - stageBox.top) / sy - 62),
        min, max,
      };
      setAnchor(current => current?.left === next.left && current?.top === next.top
        && current.min === min && current.max === max ? current : next);
    };
    const observer = new ResizeObserver(sync);
    observer.observe(stage);
    observer.observe(composer);
    const attributes = new MutationObserver(sync);
    attributes.observe(composer, { attributes: true, attributeFilter: ["class", "style"] });
    window.addEventListener("resize", sync);
    sync();
    return () => { observer.disconnect(); attributes.disconnect(); window.removeEventListener("resize", sync); };
  }, [running]); // Composer swaps its idle/steering form when a turn changes.

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReducedMotion(media.matches
      || document.documentElement.dataset.loomReducedMotion === "true");
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-loom-reduced-motion"] });
    media.addEventListener("change", sync);
    sync();
    return () => { media.removeEventListener("change", sync); observer.disconnect(); };
  }, []);

  useEffect(() => {
    const stage = buttonRef.current?.closest(".composer-stage");
    let timer: number | undefined;
    const onInput = (event: Event) => {
      if (!(event.target instanceof Element) || !event.target.closest(".composer")) return;
      setTyping(true);
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setTyping(false), 850);
    };
    stage?.addEventListener("input", onInput);
    return () => { stage?.removeEventListener("input", onInput); window.clearTimeout(timer); };
  }, []);

  useEffect(() => {
    const stage = buttonRef.current?.closest(".composer-stage");
    const sync = () => setFocused(Boolean(document.activeElement?.closest(".composer")));
    stage?.addEventListener("focusin", sync);
    stage?.addEventListener("focusout", sync);
    sync();
    return () => { stage?.removeEventListener("focusin", sync); stage?.removeEventListener("focusout", sync); };
  }, [running]);

  useEffect(() => {
    let timer: number | undefined;
    const wake = () => {
      setSleeping(false);
      window.clearTimeout(timer);
      if (!running && !approval && !focused) timer = window.setTimeout(() => setSleeping(true), 45000);
    };
    document.addEventListener("pointerdown", wake, { passive: true });
    document.addEventListener("keydown", wake);
    wake();
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("pointerdown", wake);
      document.removeEventListener("keydown", wake);
    };
  }, [running, approval, focused]);

  useEffect(() => {
    if (running || approval) setCelebrating(false);
    else if (previousRunning.current && !running && completed) setCelebrating(true);
    previousRunning.current = running;
  }, [running, approval, completed]);

  useEffect(() => {
    if (!celebrating) return;
    const timer = window.setTimeout(() => setCelebrating(false), 1100);
    return () => window.clearTimeout(timer);
  }, [celebrating]);

  useEffect(() => {
    if (!reaction) return;
    const timer = window.setTimeout(() => setReaction(null), REACTION_MS[reaction.kind]);
    return () => window.clearTimeout(timer);
  }, [reaction]);

  useLayoutEffect(() => {
    if (!reaction || reducedMotion) return;
    // A second tap of the same kind must replay the gesture, not only its emote.
    for (const animation of buttonRef.current?.getAnimations() ?? []) {
      animation.currentTime = 0;
      animation.play();
    }
  }, [reaction, reducedMotion]);

  useEffect(() => {
    if (!hovered || !interactive || sleeping || reaction) return;
    const timer = window.setTimeout(() => {
      if (performance.now() - lastHello.current < 8000) return;
      lastHello.current = performance.now();
      react("hello");
    }, 220);
    return () => window.clearTimeout(timer);
  }, [hovered, interactive, sleeping, reaction]);

  useEffect(() => { if (!interactive) setReaction(null); }, [interactive]);

  useEffect(() => {
    setBlinking(false);
    if (reducedMotion || state === "sleeping") return;
    let timer: number;
    let reopen: number;
    const schedule = () => {
      timer = window.setTimeout(() => {
        setBlinking(true);
        reopen = window.setTimeout(() => { setBlinking(false); schedule(); }, 140);
      }, 3400 + Math.random() * 3200);
    };
    schedule();
    return () => { window.clearTimeout(timer); window.clearTimeout(reopen); };
  }, [state, reducedMotion]);

  useEffect(() => {
    setPose(0);
    setEar(false);
    if (reducedMotion || state === "sleeping") return;
    let step = 0;
    const animated = Boolean(reaction) || celebrating || typing || state === "working";
    const timer = window.setInterval(() => {
      step += 1;
      // Brief tail flicks at rest; an explicit three-pose cycle during activity.
      setPose((animated ? [0, 1, 0, 2][step % 4] : step % 20 === 16 ? 1 : step % 20 === 18 ? 2 : 0) as PetPose);
      setEar(animated ? step % 4 === 1 : step % 20 === 17);
    }, reaction?.kind === "tickle" ? 120 : animated ? 240 : 420);
    return () => window.clearInterval(timer);
  }, [state, reaction, celebrating, typing, reducedMotion]);

  useEffect(() => {
    setLook(0);
    setLookY(0);
    setNearby(false);
    if (state === "sleeping" || reducedMotion) return;
    let frame: number | undefined;
    let pointerX = 0;
    let pointerY = 0;
    const move = (event: PointerEvent) => {
      pointerX = event.clientX;
      pointerY = event.clientY;
      if (frame !== undefined) return;
      frame = window.requestAnimationFrame(() => {
        frame = undefined;
        const bounds = buttonRef.current?.getBoundingClientRect();
        if (!bounds) return;
        const dx = (pointerX - bounds.left) * 64 / bounds.width - 16;
        const dy = (pointerY - bounds.top) * 64 / bounds.height - 30;
        const near = Math.abs(dx) < 140 && Math.abs(dy) < 110;
        setNearby(near);
        setLook(near && Math.abs(dx) > 22 ? dx > 0 ? 1 : -1 : 0);
        setLookY(near && Math.abs(dy) > 22 ? dy > 0 ? 1 : -1 : 0);
      });
    };
    document.addEventListener("pointermove", move, { passive: true });
    return () => {
      if (frame !== undefined) window.cancelAnimationFrame(frame);
      document.removeEventListener("pointermove", move);
    };
  }, [state, reducedMotion]);

  const onPointerMove = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!event.isPrimary) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const held = drag.current;
    if (held && held.pointer === event.pointerId && anchor) {
      const delta = (event.clientX - held.x) * 64 / bounds.width;
      if (Math.abs(delta) > 5) held.moved = true;
      if (held.moved) {
        const left = Math.round(Math.max(anchor.min, Math.min(anchor.max, held.left + delta)));
        perch.current = anchor.max > anchor.min ? (left - anchor.min) / (anchor.max - anchor.min) : 1;
        setAnchor(current => current ? { ...current, left } : current);
        setDragging(true);
        setReaction(null);
        return;
      }
    }
    const x = (event.clientX - bounds.left) * 64 / bounds.width;
    const y = (event.clientY - bounds.top) * 64 / bounds.height;
    zone.current = x >= 40 && y >= 30 ? "tail" : x < 40 && y >= 15 && y <= 42 ? "head" : "body";
    const previous = stroke.current;
    const distance = previous.distance + Math.min(8, Math.hypot(x - previous.x, y - previous.y));
    stroke.current = { x, y, distance: zone.current === "head" ? distance : 0 };
    if (interactive && !sleeping && zone.current === "head" && distance > 22
      && performance.now() - lastStroke.current > 1600) {
      lastStroke.current = performance.now();
      stroke.current.distance = 0;
      react("petting");
    }
  };

  const endDrag = (event: ReactPointerEvent<HTMLButtonElement>, cancelled = false) => {
    const held = drag.current;
    if (!held || held.pointer !== event.pointerId) return;
    drag.current = null;
    setDragging(false);
    if (held.moved) {
      suppressClick.current = true;
      if (!cancelled && interactive) react("hello");
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const onClick = (keyboard: boolean) => {
    if (suppressClick.current && !keyboard) { suppressClick.current = false; return; }
    if (approval) {
      const card = buttonRef.current?.closest(".workspace")?.querySelector<HTMLElement>(".approval-card");
      card?.scrollIntoView({ block: "nearest", behavior: reducedMotion ? "instant" : "smooth" });
      if (card) {
        if (!card.hasAttribute("tabindex")) card.tabIndex = -1;
        card.focus({ preventScroll: true });
      }
      return;
    }
    if (sleeping || wakingClick.current) { wakingClick.current = false; setSleeping(false); react("wake"); return; }
    if (running) { react("hello"); return; }
    const now = performance.now();
    const count = now - lastClick.current.at < 1100 ? Math.min(3, lastClick.current.count + 1) : 1;
    lastClick.current = { at: now, count };
    react(count >= 3 ? "delight" : keyboard ? "hop" : zone.current === "head" ? "petting"
      : zone.current === "tail" ? "tickle" : "hop");
  };

  const zh = document.documentElement.lang.startsWith("zh");
  const description = state === "working" ? (zh ? "Loom 正在工作" : "Loom is working") : state === "approval"
    ? (zh ? "查看待确认事项" : "Show pending approval") : state === "sleeping" ? (zh ? "唤醒 Loom" : "Wake Loom")
      : state === "listening" ? (zh ? "Loom 在听你说，可以摸摸它" : "Loom is listening — pet me")
        : (zh ? "摸摸头、挠挠尾巴，或沿输入框上沿拖动" : "Pet Loom — stroke my head, tickle my tail, or drag along the edge");

  const happy = reaction?.kind === "petting" || reaction?.kind === "delight" || celebrating;

  return <button ref={buttonRef} type="button" aria-label={description} title={description}
    className={`loom-pet ${celebrating ? "is-celebrating" : ""}`}
    style={anchor ? { "--pet-left": `${anchor.left}px`, "--pet-top": `${anchor.top}px` } as CSSProperties : undefined}
    data-anchored={Boolean(anchor)} data-state={state} data-reduced-motion={reducedMotion}
    data-reaction={reaction?.kind ?? "none"} data-nearby={nearby || hovered} data-typing={typing} data-dragging={dragging}
    onPointerEnter={() => { setHovered(true); stroke.current.distance = 0; zone.current = "body"; }}
    onPointerLeave={() => { setHovered(false); stroke.current.distance = 0; }}
    onPointerMove={onPointerMove}
    onPointerDown={event => {
      if (!event.isPrimary || event.button !== 0) return;
      suppressClick.current = false;
      wakingClick.current = sleeping;
      // Petting must not steal the user's insertion point or dismiss model panels.
      if (event.pointerType === "mouse" && !approval) event.preventDefault();
      if (anchor && !approval) {
        drag.current = { pointer: event.pointerId, x: event.clientX, left: anchor.left, moved: false };
        event.currentTarget.setPointerCapture(event.pointerId);
      }
      onPointerMove(event);
    }}
    onPointerUp={event => endDrag(event)} onPointerCancel={event => endDrag(event, true)}
    onLostPointerCapture={event => endDrag(event, true)}
    onKeyDown={event => {
      if (event.key === "Enter" || event.key === " ") wakingClick.current = sleeping;
      if ((event.key === "ArrowLeft" || event.key === "ArrowRight") && anchor) {
        event.preventDefault();
        const left = Math.max(anchor.min, Math.min(anchor.max, anchor.left + (event.key === "ArrowLeft" ? -4 : 4)));
        perch.current = anchor.max > anchor.min ? (left - anchor.min) / (anchor.max - anchor.min) : 1;
        setAnchor({ ...anchor, left });
      }
    }}
    onClick={event => onClick(event.detail === 0)}>
    <LoomPetArt pose={pose} ear={ear || typing || hovered || dragging} look={look} lookY={typing && focused ? 1 : lookY}
      eyes={state === "sleeping" || blinking ? "closed" : happy ? "happy" : "open"} />
    <span key={reaction?.id ?? 0} className="loom-pet-emote" aria-hidden="true">
      <svg viewBox="0 0 64 64"><path className="pet-heart pet-heart-one" d="M25 15h2v1h1v-1h2v3h-1v1h-1v1h-1v-1h-1v-1h-1z" />
        <path className="pet-heart pet-heart-two" d="M37 22h2v1h1v-1h2v3h-1v1h-1v1h-1v-1h-1v-1h-1z" />
        <path className="pet-mote pet-mote-one" d="M13 16h1v2h2v1h-2v2h-1v-2h-2v-1h2z" />
        <path className="pet-mote pet-mote-two" d="M43 17h2v2h-2z" />
        <path className="pet-mote pet-mote-three" d="M56 27h1v1h1v1h-1v1h-1v-1h-1v-1h1z" /></svg>
    </span>
    <span className="sr-only" aria-live="polite">{state === "approval"
      ? (zh ? "Loom 等待你的确认" : "Loom is waiting for approval") : state === "working" ? description : ""}</span>
  </button>;
}
