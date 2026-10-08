import rehypeHighlight from "rehype-highlight";

// react-markdown attaches plugins again on each render. Register the language
// grammars once, on first use, instead of once per user/assistant message.
let highlight: ReturnType<typeof rehypeHighlight>;
export function rehypeHighlightOnce() {
  return highlight ??= rehypeHighlight({ detect: false });
}
