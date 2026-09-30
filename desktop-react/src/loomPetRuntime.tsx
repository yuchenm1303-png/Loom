import { useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import "./loom-pet.css";

export type LoomPetState =
  | "idle"
  | "listening"
  | "working"
  | "approval"
  | "sleeping";

type PixelKey = "o" | "d" | "m" | "l" | "c" | "g" | "w" | "p";
type PixelRegion = "sky" | "head" | "body" | "tail";
type PixelRun = {
  key: PixelKey;
  x: number;
  y: number;
  width: number;
};

const SPRITE_ROWS = [
  "  cgg                           ",
  " gg                             ",
  "g       pp                      ",
  "gg                              ",
  "ggg           g                 ",
  " ggg  g                         ",
  "  ggg                           ",
  "            co  pp              ",
  "           dlo                  ",
  " g      o ollo                  ",
  " g     oo lcldo                 ",
  "      cccoccpdo olpo            ",
  "     occcmo pooolclo            ",
  "    ollllmgdopccccmo            ",
  "  moccldopgmmcccclm             ",
  "  owccgg olmmmcccmd             ",
  "  docccccoolpd cmmd             ",
  "    wcccllmllpoomo      ooo     ",
  "     ccccclllogo      occccmc   ",
  "      mccccddoggo    occcccllo  ",
  "     olmcmoollogo    cccccllll  ",
  "    occcmllmllomlm   ccccllmmlo ",
  "     occcclccool ll  occollmmmo ",
  "     occccccmmmlo lc     llggmmo",
  "      cccccwmmmdll       ommgmmo",
  "      occcodmmmmlplo    oommmgmo",
  "      odccoddmlcllll   odddmmgdm",
  "      odocdddocclllmo oddddmglo ",
  "     ddomodmmoccllmmmodddddmll  ",
  "     doccocmmoccccmmdodddmlcc   ",
  "     ocwocc occcccmmoodllcccd   ",
  "      ooooooooooooooo  ooooo    ",
] as const;

const PIXEL_FILL: Record<PixelKey, string> = {
  o: "var(--loom-pet-outline)",
  d: "var(--loom-pet-dark)",
  m: "var(--loom-pet-mid)",
  l: "var(--loom-pet-light)",
  c: "var(--loom-pet-cream)",
  g: "var(--loom-pet-gold)",
  w: "var(--loom-pet-white)",
  p: "var(--loom-pet-purple)",
};

function classifyPixel(x: number, y: number): PixelRegion {
  if (y <= 6) return "sky";
  if (x >= 20 && y >= 17) return "tail";
  if (x <= 18 && y <= 18) return "head";
  return "body";
}

function buildRuns(region: PixelRegion): PixelRun[] {
  const runs: PixelRun[] = [];

  for (let y = 0; y < SPRITE_ROWS.length; y += 1) {
    const row = SPRITE_ROWS[y];
    let x = 0;

    while (x < row.length) {
      const key = row[x] as PixelKey | " ";
      if (key === " " || classifyPixel(x, y) !== region) {
        x += 1;
        continue;
      }

      let width = 1;
      while (
        x + width < row.length
        && row[x + width] === key
        && classifyPixel(x + width, y) === region
      ) {
        width += 1;
      }

      runs.push({ key, x, y, width });
      x += width;
    }
  }

  return runs;
}

const SKY_RUNS = buildRuns("sky");
const HEAD_RUNS = buildRuns("head");
const BODY_RUNS = buildRuns("body");
const TAIL_RUNS = buildRuns("tail");

function PixelRuns({ runs }: { runs: PixelRun[] }) {
  return (
    <>
      {runs.map((run, index) => (
        <rect
          key={`${run.x}-${run.y}-${index}`}
          x={run.x}
          y={run.y}
          width={run.width}
          height={1}
          fill={PIXEL_FILL[run.key]}
        />
      ))}
    </>
  );
}

function prefersReducedMotion(): boolean {
  return document.documentElement.dataset.loomReducedMotion === "true"
    || Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
}

function deriveState(): LoomPetState {
  if (document.querySelector(".approval-card")) return "approval";
  if (document.querySelector(".conversation-stage.is-running")) return "working";
  if (document.activeElement instanceof HTMLElement && document.activeElement.closest(".composer")) {
    return "listening";
  }
  return "idle";
}

function LoomPet() {
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const previousStateRef = useRef<LoomPetState>("idle");
  const blinkTimerRef = useRef<number | null>(null);
  const celebrateTimerRef = useRef<number | null>(null);
  const playfulTimerRef = useRef<number | null>(null);
  const sleepTimerRef = useRef<number | null>(null);
  const pointerFrameRef = useRef<number | null>(null);
  const latestPointerRef = useRef({ x: 0, y: 0 });

  const [runtimeState, setRuntimeState] = useState<LoomPetState>(() => deriveState());
  const [sleeping, setSleeping] = useState(false);
  const [blinking, setBlinking] = useState(false);
  const [celebrating, setCelebrating] = useState(false);
  const [playful, setPlayful] = useState(false);
  const [look, setLook] = useState({ x: 0, y: 0 });

  const displayState: LoomPetState = sleeping && runtimeState === "idle"
    ? "sleeping"
    : runtimeState;

  useEffect(() => {
    let frame: number | null = null;

    const sync = () => {
      frame = null;
      const next = deriveState();
      setRuntimeState((current) => (current === next ? current : next));
      if (next !== "idle") setSleeping(false);
    };

    const schedule = () => {
      if (frame !== null) return;
      frame = window.requestAnimationFrame(sync);
    };

    const observer = new MutationObserver(schedule);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["class", "data-status", "aria-busy"],
    });

    document.addEventListener("focusin", schedule, true);
    document.addEventListener("focusout", schedule, true);
    schedule();

    return () => {
      if (frame !== null) window.cancelAnimationFrame(frame);
      observer.disconnect();
      document.removeEventListener("focusin", schedule, true);
      document.removeEventListener("focusout", schedule, true);
    };
  }, []);

  useEffect(() => {
    const previous = previousStateRef.current;
    previousStateRef.current = runtimeState;

    if ((previous === "working" || previous === "approval") && runtimeState !== "working" && runtimeState !== "approval") {
      setCelebrating(true);
      if (celebrateTimerRef.current !== null) window.clearTimeout(celebrateTimerRef.current);
      celebrateTimerRef.current = window.setTimeout(() => {
        setCelebrating(false);
        celebrateTimerRef.current = null;
      }, 1100);
    }
  }, [runtimeState]);

  useEffect(() => {
    const scheduleBlink = () => {
      if (blinkTimerRef.current !== null) window.clearTimeout(blinkTimerRef.current);
      const delay = 3600 + Math.round(Math.random() * 4200);
      blinkTimerRef.current = window.setTimeout(() => {
        if (displayState !== "sleeping" && !prefersReducedMotion()) {
          setBlinking(true);
          window.setTimeout(() => setBlinking(false), 130);
        }
        scheduleBlink();
      }, delay);
    };

    scheduleBlink();
    return () => {
      if (blinkTimerRef.current !== null) window.clearTimeout(blinkTimerRef.current);
    };
  }, [displayState]);

  useEffect(() => {
    const armSleep = () => {
      setSleeping(false);
      if (sleepTimerRef.current !== null) window.clearTimeout(sleepTimerRef.current);
      sleepTimerRef.current = window.setTimeout(() => {
        if (deriveState() === "idle") setSleeping(true);
      }, 45000);
    };

    document.addEventListener("pointerdown", armSleep, { capture: true, passive: true });
    document.addEventListener("keydown", armSleep, true);
    armSleep();

    return () => {
      if (sleepTimerRef.current !== null) window.clearTimeout(sleepTimerRef.current);
      document.removeEventListener("pointerdown", armSleep, true);
      document.removeEventListener("keydown", armSleep, true);
    };
  }, []);

  useEffect(() => {
    const updateLook = () => {
      pointerFrameRef.current = null;
      const button = buttonRef.current;
      if (!button || displayState === "sleeping") return;

      const rect = button.getBoundingClientRect();
      const centerX = rect.left + rect.width * 0.45;
      const centerY = rect.top + rect.height * 0.45;
      const dx = latestPointerRef.current.x - centerX;
      const dy = latestPointerRef.current.y - centerY;
      const next = {
        x: Math.abs(dx) < 18 ? 0 : dx > 0 ? 1 : -1,
        y: Math.abs(dy) < 16 ? 0 : dy > 0 ? 1 : -1,
      };
      setLook((current) => (current.x === next.x && current.y === next.y ? current : next));
    };

    const onPointerMove = (event: PointerEvent) => {
      latestPointerRef.current = { x: event.clientX, y: event.clientY };
      if (pointerFrameRef.current !== null) return;
      pointerFrameRef.current = window.requestAnimationFrame(updateLook);
    };

    document.addEventListener("pointermove", onPointerMove, { passive: true });
    return () => {
      if (pointerFrameRef.current !== null) window.cancelAnimationFrame(pointerFrameRef.current);
      document.removeEventListener("pointermove", onPointerMove);
    };
  }, [displayState]);

  useEffect(() => () => {
    if (celebrateTimerRef.current !== null) window.clearTimeout(celebrateTimerRef.current);
    if (playfulTimerRef.current !== null) window.clearTimeout(playfulTimerRef.current);
  }, []);

  const onClick = () => {
    setSleeping(false);
    setPlayful(true);
    if (playfulTimerRef.current !== null) window.clearTimeout(playfulTimerRef.current);
    playfulTimerRef.current = window.setTimeout(() => {
      setPlayful(false);
      playfulTimerRef.current = null;
    }, 720);
  };

  const eyeX = 8 + (displayState === "sleeping" ? 0 : look.x);
  const eyeY = 13 + (displayState === "sleeping" ? 0 : look.y);

  return (
    <button
      ref={buttonRef}
      type="button"
      className={`loom-pet ${celebrating ? "is-celebrating" : ""} ${playful ? "is-playful" : ""}`}
      data-state={displayState}
      aria-label="Loom companion"
      title={displayState === "working"
        ? "Loom is working"
        : displayState === "approval"
          ? "Loom needs your approval"
          : displayState === "sleeping"
            ? "Loom is napping"
            : "Loom companion"}
      onClick={onClick}
    >
      <svg
        className="loom-pet-svg"
        viewBox="0 0 32 32"
        role="img"
        aria-hidden="true"
        shapeRendering="crispEdges"
      >
        <g className="loom-pet-sky">
          <PixelRuns runs={SKY_RUNS} />
        </g>

        <g className="loom-pet-tail">
          <PixelRuns runs={TAIL_RUNS} />
        </g>

        <g className="loom-pet-body">
          <PixelRuns runs={BODY_RUNS} />
        </g>

        <g className="loom-pet-head">
          <PixelRuns runs={HEAD_RUNS} />
          {displayState === "sleeping" || blinking ? (
            <rect className="loom-pet-eye-sleep" x="7" y="13" width="2" height="1" />
          ) : (
            <>
              <rect className="loom-pet-eye-glow" x={eyeX} y={eyeY} width="1" height="1" />
              <rect className="loom-pet-eye-core" x={eyeX} y={eyeY} width="1" height="1" />
            </>
          )}
        </g>

        <g className="loom-pet-approval-mark" aria-hidden="true">
          <rect x="25" y="5" width="2" height="1" />
          <rect x="25" y="6" width="2" height="3" />
          <rect x="25" y="10" width="2" height="2" />
        </g>

        <g className="loom-pet-success-spark" aria-hidden="true">
          <rect x="25" y="4" width="1" height="5" />
          <rect x="23" y="6" width="5" height="1" />
          <rect x="28" y="10" width="1" height="3" />
          <rect x="27" y="11" width="3" height="1" />
        </g>

        <g className="loom-pet-sleep-mark" aria-hidden="true">
          <rect x="24" y="5" width="4" height="1" />
          <rect x="27" y="6" width="1" height="1" />
          <rect x="26" y="7" width="1" height="1" />
          <rect x="25" y="8" width="1" height="1" />
          <rect x="24" y="9" width="4" height="1" />
        </g>
      </svg>
      <span className="sr-only" aria-live="polite">
        {displayState === "working"
          ? "Loom is working"
          : displayState === "approval"
            ? "Loom is waiting for approval"
            : displayState === "sleeping"
              ? "Loom is sleeping"
              : ""}
      </span>
    </button>
  );
}

let runtimeRoot: Root | null = null;
let runtimeHost: HTMLDivElement | null = null;
let mountedStage: Element | null = null;
let mountFrame: number | null = null;

function unmountPet() {
  runtimeRoot?.unmount();
  runtimeRoot = null;
  runtimeHost?.remove();
  runtimeHost = null;
  mountedStage = null;
}

function mountPet() {
  mountFrame = null;
  const stage = document.querySelector(".composer-stage");
  if (!stage) {
    if (mountedStage) unmountPet();
    return;
  }

  if (stage === mountedStage && runtimeHost?.isConnected) return;
  unmountPet();

  const host = document.createElement("div");
  host.className = "loom-pet-runtime-host";
  host.setAttribute("data-loom-pet-runtime", "true");
  stage.append(host);

  runtimeHost = host;
  mountedStage = stage;
  runtimeRoot = createRoot(host);
  runtimeRoot.render(<LoomPet />);
}

function scheduleMount() {
  if (mountFrame !== null) return;
  mountFrame = window.requestAnimationFrame(mountPet);
}

const mountObserver = new MutationObserver(scheduleMount);
mountObserver.observe(document.body, { childList: true, subtree: true });
scheduleMount();

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    if (mountFrame !== null) window.cancelAnimationFrame(mountFrame);
    mountObserver.disconnect();
    unmountPet();
  });
}
