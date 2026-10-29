import "./thinking-glyph.css";

/**
 * The live "thinking" mark: one small bead that gives off a soft pulse of light.
 * The label's shimmer starts as the pulse leaves, so the light seems to travel
 * from the bead into the words. Both come from the thinking row's cadence
 * (--thinking-cycle), see inline-thinking.css.
 */
export function ThinkingGlyph() {
  return (
    <span className="tg" aria-hidden="true">
      <i className="tg-pulse" />
      <i className="tg-bead" />
    </span>
  );
}
