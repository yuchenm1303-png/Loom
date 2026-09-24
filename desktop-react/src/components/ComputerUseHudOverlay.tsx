import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useI18n } from "../i18n";

type LoomNotification = {
  method: string;
  params?: Record<string, unknown>;
};

type HudSource = "computer" | "browser";

type HudPayload = {
  visible: boolean;
  source: HudSource;
  phase: number;
  title: string;
  meta: string;
  bubbleTitle: string;
  thought: string;
  confidence: string;
  actionSource: string;
  xNorm: number;
  yNorm: number;
  clickRevision: string;
  captureSafe: boolean;
  terminal: boolean;
};

type Viewport = { width: number; height: number };

const DEFAULT_PAYLOAD: HudPayload = {
  visible: false,
  source: "computer",
  phase: 0,
  title: "Loom is observing",
  meta: "Computer Use",
  bubbleTitle: "Preparing visual automation",
  thought: "Waiting for the next browser or desktop action.",
  confidence: "—",
  actionSource: "runtime event",
  xNorm: 0.52,
  yNorm: 0.46,
  clickRevision: "",
  captureSafe: false,
  terminal: false,
};

function clamp(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, value));
}

function asNumber(value: unknown, fallback: number): number {
  if (value == null || value === "") return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function asString(value: unknown, fallback: string): string {
  const text = typeof value === "string" ? value.trim() : "";
  return text || fallback;
}

function normalizeHudPayload(raw: Record<string, unknown>, previous: HudPayload | null): HudPayload {
  const source = raw.source === "browser" ? "browser" : "computer";
  const base = previous ?? DEFAULT_PAYLOAD;
  return {
    visible: raw.visible !== false,
    source,
    phase: clamp(Math.round(asNumber(raw.phase, base.phase)), 0, 4),
    title: asString(raw.title, source === "browser" ? "Loom is controlling browser" : "Loom is controlling desktop"),
    meta: asString(raw.meta, source === "browser" ? "Browser Use" : "Computer Use"),
    bubbleTitle: asString(raw.bubbleTitle ?? raw.currentAction, base.bubbleTitle),
    thought: asString(raw.thought ?? raw.result, base.thought),
    confidence: asString(raw.confidence, base.confidence),
    actionSource: asString(raw.actionSource, source === "browser" ? "browser-use + DOM" : "screenshot + UIA"),
    xNorm: clamp(asNumber(raw.xNorm, base.xNorm), 0, 1),
    yNorm: clamp(asNumber(raw.yNorm, base.yNorm), 0, 1),
    clickRevision: asString(raw.clickRevision, ""),
    captureSafe: raw.captureSafe === true,
    terminal: raw.terminal === true,
  };
}

function useViewport(): Viewport {
  const [viewport, setViewport] = useState<Viewport>(() => ({
    width: typeof window === "undefined" ? 1440 : window.innerWidth,
    height: typeof window === "undefined" ? 900 : window.innerHeight,
  }));

  useEffect(() => {
    const update = () => setViewport({ width: window.innerWidth, height: window.innerHeight });
    update();
    window.addEventListener("resize", update, { passive: true });
    return () => window.removeEventListener("resize", update);
  }, []);

  return viewport;
}

export function ComputerUseHudOverlay() {
  const { language } = useI18n();
  const viewport = useViewport();
  const [payload, setPayload] = useState<HudPayload | null>(null);
  const [visible, setVisible] = useState(false);
  const hideTimer = useRef<number | null>(null);
  const lastClickRevision = useRef("");
  const [clickPulse, setClickPulse] = useState(0);

  useEffect(() => {
    return () => {
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    };
  }, []);

  useEffect(() => {
    const bridge = (window as unknown as { loom?: { onNotification?: (listener: (payload: LoomNotification) => void) => () => void } }).loom;
    if (!bridge?.onNotification) return;

    const scheduleHide = (delayMs: number) => {
      if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
      hideTimer.current = window.setTimeout(() => {
        setVisible(false);
        hideTimer.current = null;
      }, delayMs);
    };

    const unsubscribe = bridge.onNotification((message) => {
      if (message.method === "hud/update") {
        const raw = message.params ?? {};
        if (raw.visible === false) {
          scheduleHide(420);
          return;
        }
        if (hideTimer.current !== null) {
          window.clearTimeout(hideTimer.current);
          hideTimer.current = null;
        }
        setPayload((previous) => {
          const next = normalizeHudPayload(raw, previous);
          if (next.clickRevision && next.clickRevision !== lastClickRevision.current) {
            lastClickRevision.current = next.clickRevision;
            setClickPulse((value) => value + 1);
          }
          return next;
        });
        setVisible(true);
        if (raw.terminal === true || asNumber(raw.phase, 0) >= 4) scheduleHide(2100);
        return;
      }

      if (message.method === "turn/completed") scheduleHide(850);
    });

    return unsubscribe;
  }, []);

  const state = payload ?? DEFAULT_PAYLOAD;
  const phases = language === "zh-CN"
    ? ["观察", "分析", "移动", "点击", "验证"]
    : ["Observe", "Analyze", "Move", "Act", "Verify"];
  const sourceLabel = state.source === "browser"
    ? language === "zh-CN" ? "浏览器自动化" : "Browser Use"
    : language === "zh-CN" ? "桌面控制" : "Computer Use";

  const geometry = useMemo(() => {
    const width = Math.max(1, viewport.width);
    const height = Math.max(1, viewport.height);
    const x = clamp(state.xNorm, 0, 1) * width;
    const y = clamp(state.yNorm, 0, 1) * height;
    const bubbleWidth = Math.min(420, Math.max(320, width - 48));
    const bubbleHeight = 142;
    const cursorGap = 52;
    const bubbleX = clamp(
      x > width * 0.58 ? x - bubbleWidth - cursorGap : x + cursorGap,
      18,
      Math.max(18, width - bubbleWidth - 18),
    );
    const bubbleY = clamp(
      y > height * 0.58 ? y - bubbleHeight - 30 : y + 26,
      18,
      Math.max(18, height - bubbleHeight - 18),
    );
    return { x, y, bubbleX, bubbleY, bubbleWidth };
  }, [state.xNorm, state.yNorm, viewport.height, viewport.width]);

  const style = {
    "--hud-x": `${geometry.x}px`,
    "--hud-y": `${geometry.y}px`,
    "--hud-bubble-x": `${geometry.bubbleX}px`,
    "--hud-bubble-y": `${geometry.bubbleY}px`,
    "--hud-bubble-width": `${geometry.bubbleWidth}px`,
  } as CSSProperties;

  return (
    <div
      className={[
        "computer-use-hud",
        visible && state.visible ? "hud-live" : "",
        state.captureSafe ? "capture-safe" : "",
        `source-${state.source}`,
      ].filter(Boolean).join(" ")}
      style={style}
      aria-hidden="true"
    >
      <div className="hud-edge-aurora">
        <div className="hud-edge-layer hud-edge-cast" />
        <div className="hud-edge-layer hud-edge-halo" />
        <div className="hud-edge-vignette" />
      </div>

      <div className="hud-top-pill">
        <span className="hud-status-dot" />
        <strong>{state.title}</strong>
        <span>{state.meta || sourceLabel}</span>
      </div>

      <div className={`hud-cursor-wrap ${clickPulse ? "clicking" : ""}`} key={`cursor-${clickPulse}`}>
        <div className="hud-cursor-aura" />
        <svg className="hud-cursor-icon" viewBox="0 0 64 64" focusable="false">
          <defs>
            <path id="loomHudMouseCursorShape" d="M 7.8 7.8 C 8.91 5.71, 13.83 7.17, 20.4 10.4 C 26.97 13.63, 49.02 25.34, 51.1 29.1 C 53.18 32.86, 37.42 31.38, 34.1 35.2 C 30.78 39.02, 30.93 52.78, 29.2 54.3 C 27.47 55.82, 25.14 49.77, 22.7 45.2 C 20.26 40.63, 15.36 29.87, 13.1 24.2 C 10.84 18.53, 6.69 9.89, 7.8 7.8 Z" />
            <linearGradient id="loomHudMouseCursorBase" x1="9" y1="9" x2="42" y2="55" gradientUnits="userSpaceOnUse">
              <stop offset="0" stopColor="#2EF1FF" />
              <stop offset=".23" stopColor="#69E9FF" />
              <stop offset=".54" stopColor="#EDF7FF" />
              <stop offset=".78" stopColor="#E6D9FF" />
              <stop offset="1" stopColor="#FF9FCE" />
            </linearGradient>
            <radialGradient id="loomHudMouseCursorCyan" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(33.7 16) rotate(33) scale(23 18)">
              <stop offset="0" stopColor="#08EAFF" stopOpacity=".95" />
              <stop offset=".42" stopColor="#2EDCFF" stopOpacity=".54" />
              <stop offset="1" stopColor="#2EDCFF" stopOpacity="0" />
            </radialGradient>
            <radialGradient id="loomHudMouseCursorWhite" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(29 28) rotate(32) scale(18 14)">
              <stop offset="0" stopColor="#FFFFFF" stopOpacity=".96" />
              <stop offset=".36" stopColor="#FFFFFF" stopOpacity=".80" />
              <stop offset=".72" stopColor="#EEF7FF" stopOpacity=".18" />
              <stop offset="1" stopColor="#EEF7FF" stopOpacity="0" />
            </radialGradient>
            <radialGradient id="loomHudMouseCursorPink" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(30 50) rotate(-70) scale(18 18)">
              <stop offset="0" stopColor="#FF92CD" stopOpacity=".76" />
              <stop offset=".46" stopColor="#B08CFF" stopOpacity=".26" />
              <stop offset="1" stopColor="#B08CFF" stopOpacity="0" />
            </radialGradient>
            <linearGradient id="loomHudMouseCursorRim" x1="9" y1="8" x2="42" y2="56" gradientUnits="userSpaceOnUse">
              <stop offset="0" stopColor="#CCFEFF" stopOpacity=".97" />
              <stop offset=".30" stopColor="#78F0FF" stopOpacity=".94" />
              <stop offset=".66" stopColor="#C0BEFF" stopOpacity=".86" />
              <stop offset="1" stopColor="#FFC5E2" stopOpacity=".95" />
            </linearGradient>
            <clipPath id="loomHudMouseCursorClip"><use href="#loomHudMouseCursorShape" /></clipPath>
            <filter id="loomHudMouseCursorGlow" x="-80%" y="-80%" width="260%" height="260%">
              <feGaussianBlur in="SourceGraphic" stdDeviation="1.45" result="blur" />
              <feColorMatrix in="blur" type="matrix" values="0 0 0 0 0.20 0 0 0 0 0.82 0 0 0 0 1 0 0 0 .18 0" result="cyanGlow" />
              <feMerge><feMergeNode in="cyanGlow" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
            <filter id="loomHudMouseSoftBlur" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="2.5" />
            </filter>
          </defs>
          <g filter="url(#loomHudMouseCursorGlow)">
            <use href="#loomHudMouseCursorShape" fill="url(#loomHudMouseCursorBase)" />
            <use href="#loomHudMouseCursorShape" fill="url(#loomHudMouseCursorCyan)" />
            <use href="#loomHudMouseCursorShape" fill="url(#loomHudMouseCursorWhite)" />
            <use href="#loomHudMouseCursorShape" fill="url(#loomHudMouseCursorPink)" />
            <use href="#loomHudMouseCursorShape" fill="none" stroke="url(#loomHudMouseCursorRim)" strokeWidth="1.02" strokeLinejoin="round" />
            <use href="#loomHudMouseCursorShape" fill="none" stroke="rgba(255,255,255,.22)" strokeWidth=".42" strokeLinejoin="round" />
            <g clipPath="url(#loomHudMouseCursorClip)">
              <ellipse cx="28.2" cy="28.1" rx="11.8" ry="8.2" fill="#FFFFFF" opacity=".09" filter="url(#loomHudMouseSoftBlur)" />
              <path d="M12 12.8 C20.9 14.7 33.4 20.3 45.8 27.7" fill="none" stroke="rgba(255,255,255,.44)" strokeWidth=".90" strokeLinecap="round" />
            </g>
          </g>
        </svg>
        <div className="hud-cursor-hotspot" />
        <div className="hud-click-wave" />
      </div>

      <div className="hud-info-bubble">
        <div className="hud-bubble-title">{state.bubbleTitle}</div>
        <div className="hud-bubble-line"><span>{language === "zh-CN" ? "目标置信度" : "Target confidence"}</span><b>{state.confidence}</b></div>
        <div className="hud-bubble-line"><span>{language === "zh-CN" ? "执行坐标" : "Target point"}</span><b>{Math.round(geometry.x)}, {Math.round(geometry.y)}</b></div>
        <div className="hud-bubble-line"><span>{language === "zh-CN" ? "动作来源" : "Action source"}</span><b>{state.actionSource}</b></div>
        <div className="hud-bubble-thought">{state.thought}</div>
      </div>

      <div className="hud-bottom-timeline">
        {phases.map((label, index) => (
          <div key={label} className={["hud-phase", index < state.phase ? "done" : "", index === state.phase ? "active" : ""].filter(Boolean).join(" ")}>
            <i />{label}
          </div>
        ))}
      </div>
    </div>
  );
}
