import { PET_ARTWORK } from "./loomPetArtwork";
import { useId } from "react";

export type PetPose = 0 | 1 | 2;
type Layer = keyof typeof PET_ARTWORK;

function Contours({ layer }: { layer: Layer }) {
  return <>{PET_ARTWORK[layer].map(({ fill, d }) => <path key={fill} fill={fill} d={d} />)}</>;
}

/** Reference-derived vector pixels. Small integer poses retain the original
 * proportions; the tail root is covered by the body in every animation frame. */
export function LoomPetArt({ pose = 0, eyes = "open", look = 0, lookY = 0, ear = false }: {
  pose?: PetPose; eyes?: "open" | "closed" | "happy"; look?: number; lookY?: number; ear?: boolean;
}) {
  const id = useId();
  return <svg className="loom-pet-svg" viewBox="0 0 64 64" aria-hidden="true" shapeRendering="crispEdges">
    <defs>
      <clipPath id={`${id}-tip`}><path d="M0 0h64v51H0z" /></clipPath>
      <clipPath id={`${id}-root`}><path d="M0 49h64v13H0z" /></clipPath>
      <clipPath id={`${id}-iris`}><path d="M14 30h4v3h-4z" /></clipPath>
    </defs>
    <g className="loom-pet-sky"><Contours layer="sky" /></g>
    <g className="loom-pet-tail">
      <g clipPath={`url(#${id}-root)`}><Contours layer="tail" /></g>
      <g clipPath={`url(#${id}-tip)`}>
        <g transform={pose === 1 ? "translate(-1 -1)" : pose === 2 ? "translate(1 0)" : undefined}><Contours layer="tail" /></g>
      </g>
    </g>
    <g className="loom-pet-body"><Contours layer="body" /></g>
    <g className="loom-pet-head">
      <Contours layer="head" />
      <g className="loom-pet-ear" transform={ear ? "translate(0 1)" : undefined}><Contours layer="ear" /></g>
      <path d="M14 29h5v4h-5z" fill="#fff2d9" />
      <g className="loom-pet-eye">
        {eyes === "open" ? <>
          <Contours layer="eye" />
          {(look !== 0 || lookY !== 0) && <g clipPath={`url(#${id}-iris)`}>
            <path d="M15 30h2v3h-2z" fill="#fff2d9" />
            <path d={`M${15 + look} ${30 + lookY}h2v2h-1v1h-1z`} fill="#ffd67e" />
            <path d={`M${16 + look} ${30 + lookY}h1v1h-1z`} fill="#c98562" />
            <path d={`M${15 + look} ${30 + lookY}h1v1h-1z`} fill="#fff9ed" />
          </g>}
        </> : <path d={eyes === "happy" ? "M14 31h1v-1h2v1h1v1h-1v-1h-2v1h-1z" : "M14 31h2v1h3v1h-4v-1h-1z"} fill="#211747" />}
      </g>
    </g>
    <g className="loom-pet-approval-mark"><path d="M47 12h2v7h-2zM47 21h2v2h-2z" fill="#ffca64" /></g>
    <g className="loom-pet-success-spark"><path d="M48 15h1v2h2v1h-2v2h-1v-2h-2v-1h2zM56 23h1v1h1v1h-1v1h-1v-1h-1v-1h1z" fill="#ffd67e" /></g>
    <g className="loom-pet-sleep-mark"><path d="M47 14h6v1h-2v1h-1v1h-1v1h4v1h-6v-1h1v-1h1v-1h1v-1h-3z" fill="#9680d1" /></g>
    <g className="loom-pet-thoughts" fill="#ffd67e"><path className="pet-thought-one" d="M42 26h1v1h-1z" />
      <path className="pet-thought-two" d="M46 23h2v2h-2z" /><path className="pet-thought-three" d="M51 20h2v2h-2z" /></g>
  </svg>;
}
