import { useId } from "react";
import "./thinking-glyph.css";

// The orbit, in the glyph's unrotated frame. It starts at the left end and runs
// along the near (lower) half first, so the thread crosses in front of the bead
// left to right, toward the label.
const ORBIT = "M-9.8 0A9.8 3.3 0 0 0 9.8 0A9.8 3.3 0 0 0 -9.8 0";
const ORBIT_TILT = "rotate(-20)";
const PLIES = [1, 2, 3, 4] as const;

function Thread() {
  return (
    <>
      <path className="tg-track" d={ORBIT} />
      {PLIES.map((ply) => (
        <path key={ply} className={`tg-ply tg-ply-${ply}`} d={ORBIT} pathLength={100} />
      ))}
    </>
  );
}

/**
 * The live "thinking" mark: a thread winding around a bead, Loom's icon in
 * motion. The orbit is drawn twice, the far half beneath the bead and the near
 * half over it, so the thread passes behind the bead and then in front of it.
 */
export function ThinkingGlyph() {
  // useId output is not a safe url(#...) fragment, so keep only word characters.
  const id = `tg-${useId().replace(/[^\w-]/g, "")}`;

  return (
    <svg className="tg" viewBox="-12 -12 24 24" aria-hidden="true" focusable="false">
      <defs>
        <radialGradient id={`${id}-halo`}>
          <stop offset="0" className="tg-halo" />
          <stop offset="1" className="tg-halo" stopOpacity="0" />
        </radialGradient>
        <linearGradient id={`${id}-bead`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" className="tg-bead-top" />
          <stop offset=".5" className="tg-bead-mid" />
          <stop offset="1" className="tg-bead-low" />
        </linearGradient>
        <radialGradient id={`${id}-lift`} cx="28%" cy="38%" r="62%">
          <stop offset="0" className="tg-lift" />
          <stop offset="1" className="tg-lift" stopOpacity="0" />
        </radialGradient>
        <clipPath id={`${id}-far`}>
          <rect x="-12" y="-12" width="24" height="12" />
        </clipPath>
        <clipPath id={`${id}-near`}>
          <rect x="-12" y="0" width="24" height="12" />
        </clipPath>
      </defs>

      <circle r="7.2" fill={`url(#${id}-halo)`} />
      <g className="tg-far" transform={ORBIT_TILT}>
        <g clipPath={`url(#${id}-far)`}><Thread /></g>
      </g>
      <circle r="4.4" fill={`url(#${id}-bead)`} />
      <circle r="4.4" fill={`url(#${id}-lift)`} />
      <g transform={ORBIT_TILT}>
        <g clipPath={`url(#${id}-near)`}><Thread /></g>
      </g>
    </svg>
  );
}
