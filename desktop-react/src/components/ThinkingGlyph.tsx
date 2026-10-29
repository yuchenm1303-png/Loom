import mark from "../assets/loom-mark-64.png";
import "./thinking-glyph.css";

/**
 * The live "working" mark: Loom's own mark, unchanged, brought to life by light that follows its drawing. A bright
 * run of light travels the orbit ribbon round the sphere toward the arrowhead (a turning wedge, cut to the ribbon by
 * the mark itself), the arrowhead flares as it arrives, and the sphere's glow breathes. Transforms and opacity only,
 * all on the thinking row's cadence (--thinking-cycle, see inline-thinking.css).
 */
export function ThinkingGlyph() {
  return (
    <span className="tg" aria-hidden="true">
      <i className="tg-pulse" />
      <img className="tg-mark" src={mark} alt="" draggable={false} />
      <span className="tg-shine"><i className="tg-sweep" /><i className="tg-flare" /></span>
    </span>
  );
}
