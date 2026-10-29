import { useId, type CSSProperties } from "react";
import {
  ARROW,
  ARROW_DIRECTION,
  EDGES_LOWER,
  EDGES_UPPER_BACK,
  EDGES_UPPER_FRONT,
  MARK_VIEWBOX,
  RIBBON_LOWER,
  RIBBON_UPPER_BACK,
  RIBBON_UPPER_FRONT,
  ROUTE,
  SPHERE,
} from "./loomMarkGeometry";
import "./loom-working-mark.css";

/** How far the arrowhead pushes forward when the light reaches it, in the mark's 512 space (about 1px at 22px). */
const ARROW_PUSH = 26;

/**
 * A live "working" mark, kept for later: not in use yet (the thinking row draws ThinkingGlyph's bead). Loom's mark
 * rebuilt from its own geometry (loomMarkGeometry.ts, fitted to the original) and drawn as the original is, in
 * glass: deep blue bodies with lit edges, a glass sphere, a soft glow. Preview: scripts/fixtures/loom-working-mark.html.
 *
 * Two layers. The base (ribbons, sphere, glow) never changes, so its glow filter is painted once. The motion layer
 * above it holds what moves, all taken from the mark's structure: a run of light travels the ribbon's centreline from
 * its tail round the sphere and up into the arrowhead (cut to the ribbon, hidden where the ribbon is behind the
 * sphere), and the arrowhead pushes forward along its own direction and lights up as the light arrives. Behind both,
 * the sphere's glow pulses. One cycle of --thinking-cycle (the thinking row's cadence, inline-thinking.css).
 */
export function LoomWorkingMark() {
  const id = `lm${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const url = (name: string) => `url(#${id}${name})`;
  const push = {
    "--lm-push-x": `${(ARROW_DIRECTION.x * ARROW_PUSH).toFixed(1)}px`,
    "--lm-push-y": `${(ARROW_DIRECTION.y * ARROW_PUSH).toFixed(1)}px`,
  } as CSSProperties;
  return (
    <span className="lm" aria-hidden="true">
      <i className="lm-pulse" />
      <svg className="lm-base" viewBox={MARK_VIEWBOX} aria-hidden="true" focusable="false">
        <defs>
          <linearGradient id={`${id}body`} gradientUnits="userSpaceOnUse" x1="50" y1="90" x2="480" y2="440">
            <stop className="lm-stop-b1" offset="0" />
            <stop className="lm-stop-b2" offset=".4" />
            <stop className="lm-stop-b3" offset=".72" />
            <stop className="lm-stop-b4" offset="1" />
          </linearGradient>
          <linearGradient id={`${id}edge`} gradientUnits="userSpaceOnUse" x1="50" y1="90" x2="480" y2="440">
            <stop className="lm-stop-e1" offset="0" />
            <stop className="lm-stop-e2" offset=".45" />
            <stop className="lm-stop-e3" offset=".78" />
            <stop className="lm-stop-e4" offset="1" />
          </linearGradient>
          <radialGradient id={`${id}sphere`} gradientUnits="userSpaceOnUse" cx={SPHERE.cx - 4} cy={SPHERE.cy - 10} r={SPHERE.r * 1.06}>
            <stop className="lm-stop-s1" offset="0" />
            <stop className="lm-stop-s2" offset=".18" />
            <stop className="lm-stop-s3" offset=".5" />
            <stop className="lm-stop-s4" offset=".82" />
            <stop className="lm-stop-s5" offset="1" />
          </radialGradient>
          <filter id={`${id}glow`} x="-20%" y="-20%" width="140%" height="140%" colorInterpolationFilters="sRGB">
            <feGaussianBlur in="SourceAlpha" stdDeviation="9" result="blur" />
            <feFlood className="lm-glow-colour" />
            <feComposite in2="blur" operator="in" />
            <feMerge>
              <feMergeNode />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <g filter={url("glow")}>
          <path d={RIBBON_UPPER_BACK} fill={url("body")} />
          <path d={EDGES_UPPER_BACK} className="lm-edge-halo" stroke={url("edge")} />
          <path d={EDGES_UPPER_BACK} className="lm-edge" stroke={url("edge")} />
          <path d={RIBBON_LOWER} fill={url("body")} />
          <path d={EDGES_LOWER} className="lm-edge-halo" stroke={url("edge")} />
          <path d={EDGES_LOWER} className="lm-edge" stroke={url("edge")} />
          <circle cx={SPHERE.cx} cy={SPHERE.cy} r={SPHERE.r} fill={url("sphere")} />
          <circle className="lm-sphere-rim" cx={SPHERE.cx} cy={SPHERE.cy} r={SPHERE.r - 4} />
          {/* The light the glass catches on its upper left. */}
          <path className="lm-sphere-glint" d={glint(SPHERE)} />
          <path d={RIBBON_UPPER_FRONT} className="lm-front" fill={url("body")} />
          <path d={EDGES_UPPER_FRONT} className="lm-edge-halo" stroke={url("edge")} />
          <path d={EDGES_UPPER_FRONT} className="lm-edge" stroke={url("edge")} />
        </g>
      </svg>
      <svg className="lm-motion" viewBox={MARK_VIEWBOX} aria-hidden="true" focusable="false">
        <defs>
          <linearGradient id={`${id}arrow`} gradientUnits="userSpaceOnUse" x1="355" y1="92" x2="445" y2="235">
            <stop className="lm-stop-a1" offset="0" />
            <stop className="lm-stop-a2" offset=".5" />
            <stop className="lm-stop-a3" offset="1" />
          </linearGradient>
          {/* Where light may lie: the ribbon and the arrowhead, less the sphere wherever the ribbon is behind it. */}
          <mask id={`${id}lit`} maskUnits="userSpaceOnUse" x="0" y="0" width="512" height="512">
            <path d={RIBBON_UPPER_BACK} fill="#fff" />
            <path d={RIBBON_LOWER} fill="#fff" />
            <circle cx={SPHERE.cx} cy={SPHERE.cy} r={SPHERE.r} fill="#000" />
            <path d={RIBBON_UPPER_FRONT} fill="#fff" />
            <path d={ARROW} fill="#fff" />
          </mask>
        </defs>
        <g className="lm-light" mask={url("lit")}>
          <path className="lm-run lm-run-tail" d={ROUTE} pathLength={100} />
          <path className="lm-run lm-run-body" d={ROUTE} pathLength={100} />
          <path className="lm-run lm-run-head" d={ROUTE} pathLength={100} />
        </g>
        <g className="lm-arrow" style={push}>
          <path d={ARROW} fill={url("arrow")} className="lm-arrow-body" />
          <path className="lm-arrow-lit" d={ARROW} />
        </g>
      </svg>
    </span>
  );
}

/** A thin crescent of light along the sphere's upper-left rim. */
function glint({ cx, cy, r }: { cx: number; cy: number; r: number }) {
  const at = (deg: number, radius: number) => {
    const a = (deg * Math.PI) / 180;
    return `${(cx + Math.cos(a) * radius).toFixed(0)} ${(cy + Math.sin(a) * radius).toFixed(0)}`;
  };
  // Out along the rim, back along a flatter arc: thick in the middle, sharp at both ends.
  const rim = r - 10;
  const back = r * 1.5;
  return `M${at(196, rim)} A${rim} ${rim} 0 0 1 ${at(262, rim)} A${back} ${back} 0 0 0 ${at(196, rim)}Z`;
}
