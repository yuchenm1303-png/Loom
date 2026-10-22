import { useEffect, useState } from "react";

/**
 * False on a component's first render, true once a frame holding that render has been painted. A surface opened on
 * demand paints its frame (its chrome, its background, the start of its entrance) in the frame after the click and
 * fills in its costly body a frame later, while the entrance is already playing on the compositor: the first frame
 * was waiting for the whole page to be rendered, styled and laid out.
 */
export function usePaintedOnce(): boolean {
  const [painted, setPainted] = useState(false);
  useEffect(() => {
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => setPainted(true));
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
    };
  }, []);
  return painted;
}
