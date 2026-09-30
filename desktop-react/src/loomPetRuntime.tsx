import { useEffect, useRef, useState } from "react";
import { LoomPetArt, type PetPose } from "./LoomPetArt";
import "./loom-pet.css";

export type LoomPetState = "idle" | "listening" | "working" | "approval" | "sleeping";
type Props = { running: boolean; approval: boolean; completed: boolean };

/** React owns this pet alongside the composer. No page-wide observers or extra roots. */
export function LoomPet({ running, approval, completed }: Props) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [focused, setFocused] = useState(false);
  const [sleeping, setSleeping] = useState(false);
  const [blinking, setBlinking] = useState(false);
  const [playful, setPlayful] = useState(false);
  const [celebrating, setCelebrating] = useState(false);
  const [look, setLook] = useState(0);
  const [pose, setPose] = useState<PetPose>(0);
  const [ear, setEar] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);
  const previousRunning = useRef(running);
  const state: LoomPetState = approval ? "approval" : running ? "working"
    : focused ? "listening" : sleeping ? "sleeping" : "idle";

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
    const sync = () => setFocused(Boolean(document.activeElement?.closest(".composer")));
    stage?.addEventListener("focusin", sync);
    stage?.addEventListener("focusout", sync);
    sync();
    return () => { stage?.removeEventListener("focusin", sync); stage?.removeEventListener("focusout", sync); };
  }, []);

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
    if (!playful) return;
    const timer = window.setTimeout(() => setPlayful(false), 800);
    return () => window.clearTimeout(timer);
  }, [playful]);

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
    const animated = playful || celebrating || state === "working";
    const timer = window.setInterval(() => {
      step += 1;
      // Brief tail flicks at rest; an explicit three-pose cycle during activity.
      setPose((animated ? [0, 1, 0, 2][step % 4] : step % 20 === 16 ? 1 : step % 20 === 18 ? 2 : 0) as PetPose);
      setEar(animated ? step % 4 === 1 : step % 20 === 17);
    }, animated ? 220 : 360);
    return () => window.clearInterval(timer);
  }, [state, playful, celebrating, reducedMotion]);

  useEffect(() => {
    setLook(0);
    if (state === "sleeping" || reducedMotion) return;
    let frame: number | undefined;
    let pointerX = 0;
    const move = (event: PointerEvent) => {
      pointerX = event.clientX;
      if (frame !== undefined) return;
      frame = window.requestAnimationFrame(() => {
        frame = undefined;
        const bounds = buttonRef.current?.getBoundingClientRect();
        if (!bounds) return;
        const dx = pointerX - (bounds.left + 21);
        setLook(Math.abs(dx) < 28 ? 0 : dx > 0 ? 1 : -1);
      });
    };
    document.addEventListener("pointermove", move, { passive: true });
    return () => {
      if (frame !== undefined) window.cancelAnimationFrame(frame);
      document.removeEventListener("pointermove", move);
    };
  }, [state, reducedMotion]);

  const description = state === "working" ? "Loom is working" : state === "approval"
    ? "Loom is waiting for approval" : state === "sleeping" ? "Loom is napping"
      : state === "listening" ? "Loom is listening" : "Loom companion";

  return <button ref={buttonRef} type="button" aria-label={description} title={description}
    className={`loom-pet ${playful ? "is-playful" : ""} ${celebrating ? "is-celebrating" : ""}`}
    data-state={state} data-reduced-motion={reducedMotion}
    onClick={() => { setSleeping(false); setPlayful(true); }}>
    <LoomPetArt pose={pose} ear={ear} look={look}
      eyes={state === "sleeping" || blinking ? "closed" : playful || celebrating ? "happy" : "open"} />
    <span className="sr-only" aria-live="polite">{state === "working" || state === "approval" ? description : ""}</span>
  </button>;
}
