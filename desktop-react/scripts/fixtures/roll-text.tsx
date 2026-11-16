import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { RollText } from "../../src/components/RollText";
import "../../src/renderer-styles";

document.documentElement.dataset.loomTheme = new URLSearchParams(location.search).get("theme") ?? "dark";
const root = createRoot(document.getElementById("root")!);
function show(text: string) {
  flushSync(() => root.render(<StrictMode><div style={{ padding: 40 }} className="inline-thinking">
    <span className="thinking-shimmer"><RollText text={text} identity={text} /></span>
  </div></StrictMode>));
}
Object.assign(window, { rollFixture: { show } });
show("Thinking…");
