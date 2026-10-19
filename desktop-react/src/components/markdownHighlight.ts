import rehypeHighlight from "rehype-highlight";
import type { Element, Root } from "hast";
import { RetainedBudgetMap } from "../state/retainedBudgetMap";

type Highlight = ReturnType<typeof rehypeHighlight>;
type HighlightedCode = Pick<Element, "properties" | "children"> & { key: string };

/** Reuse completed code across streaming parses, with bounded retained memory. */
export function createCachedHighlighter(transform: Highlight): Highlight {
  const cache = new RetainedBudgetMap<HighlightedCode>(128, 2 * 1024 * 1024, 256 * 1024);
  return (tree, file) => {
    const visit = (parent: Root | Element) => {
      if (parent.type === "element" && parent.tagName === "pre" && parent.children.length !== 1) {
        transform({ type: "root", children: [parent] }, file);
        return;
      }
      for (const node of parent.children) {
        if (node.type !== "element") continue;
        if (parent.type !== "element" || parent.tagName !== "pre" || node.tagName !== "code") {
          visit(node);
          continue;
        }
        // Only standard plain-text code nodes are cacheable. Keep unusual
        // plugin output, large blocks and diagnostics on the original path.
        const text = node.children.length === 1 && node.children[0].type === "text" ? node.children[0].value : null;
        const key = text !== null && text.length <= 32768 ? JSON.stringify([node.properties, text]) : null;
        const cached = key ? cache.get(key) : undefined;
        if (cached) {
          cache.set(cached.key, cached);
          const copy = structuredClone(cached);
          node.properties = copy.properties;
          node.children = copy.children;
          continue;
        }
        const children = node.children;
        const messages = file.messages.length;
        transform({ type: "root", children: [parent] }, file);
        if (key && node.children !== children && file.messages.length === messages) {
          // Cache no source positions: generated highlight tokens have none;
          // the code element keeps this parse's own position and data.
          cache.set(key, structuredClone({ key, properties: node.properties, children: node.children }));
        }
      }
    };
    visit(tree);
  };
}

// react-markdown attaches plugins again on each render. Register the language
// grammars once, on first use, instead of once per user/assistant message.
let highlight: Highlight;
export function rehypeHighlightOnce() {
  return highlight ??= createCachedHighlighter(rehypeHighlight({ detect: false }));
}
