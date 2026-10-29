import mark from "../assets/loom-mark-64.png";

/** Loom's mark (the glass orb in its orbit), bundled so it resolves under the desktop shell's `./` base too. The 64px
    copy covers the small places it is drawn (up to 32px on a 2x display). */
export function LoomMark({ size, className }: { size: number; className?: string }) {
  return (
    <img
      className={className ? `loom-mark ${className}` : "loom-mark"}
      src={mark}
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
      draggable={false}
    />
  );
}
