import markSmall from "../assets/loom-mark-64.png";
import markLarge from "../assets/loom-mark-160.png";

/** Loom's mark (the glass orb in its orbit), bundled so it resolves under the desktop shell's `./` base too. Small
    places draw the 64px copy; the home screen's larger one draws the 160px copy, enough for 2x displays. */
export function LoomMark({ size, className }: { size: number; className?: string }) {
  return (
    <img
      className={className ? `loom-mark ${className}` : "loom-mark"}
      src={size > 32 ? markLarge : markSmall}
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
      draggable={false}
    />
  );
}
