import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import "../../src/App";
import { Transcript } from "../../src/components/Transcript";
import { MarkdownMessage } from "../../src/components/MarkdownMessage";
import { ImageLightbox } from "../../src/components/ImageLightbox";
import { DecisionPromptCard } from "../../src/components/DecisionPromptCard";
import { useMotionPresence } from "../../src/motion/useMotionPresence";
import { I18nProvider } from "../../src/i18n";
import { applyThemePreference } from "../../src/theme";
import type { TranscriptItem } from "../../src/types/loom";
import "../../src/renderer-styles";

applyThemePreference(new URLSearchParams(location.search).get("theme") === "dark" ? "dark" : "light", { animate: false, syncNative: false });
document.documentElement.dataset.loomReducedMotion = "false";
const root = createRoot(document.getElementById("root")!);
const decision = { title: "选择方案", allowCustomInput: true, options: [
  { id: "one", title: "第一个方案" }, { id: "two", title: "第二个方案" },
] };
function Presence({ open, identity }: { open: boolean; identity: string }) {
  const presence = useMotionPresence(open, 240, identity);
  return presence.mounted ? <div id="presence" data-motion-phase={presence.phase} inert={!open}><button>Action</button></div> : null;
}
function Lightbox() {
  const [open, setOpen] = useState(false);
  return <><button id="preview" onClick={() => setOpen(true)}>Preview</button>
    <ImageLightbox open={open} label="Preview" source="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='80' height='80'/%3E" path="fixture.png" onClose={() => setOpen(false)} onReveal={() => {}} /></>;
}
const api = {
  presence(open: boolean, identity = "one") { flushSync(() => root.render(<StrictMode><Presence open={open} identity={identity} /></StrictMode>)); },
  plain(content: string, streaming: boolean) { flushSync(() => root.render(<StrictMode><MarkdownMessage content={content} streaming={streaming} /></StrictMode>)); },
  turn(items: TranscriptItem[], running: boolean) { flushSync(() => root.render(<StrictMode><I18nProvider><div className="app-shell" style={{ display: "block", height: "100vh", minHeight: 0 }}>
    <div className="conversation-stage" style={{ height: "100%" }}>
    <Transcript items={items} running={running} currentTurnId="turn-1" onApproval={() => {}} />
    </div>
    </div></I18nProvider></StrictMode>)); },
  lightbox() { flushSync(() => root.render(<StrictMode><Lightbox /></StrictMode>)); },
  card(fail = false) { flushSync(() => root.render(<StrictMode><I18nProvider>
    <DecisionPromptCard spec={decision} onSubmit={async () => {
      document.body.dataset.decisionCalls = String(Number(document.body.dataset.decisionCalls || 0) + 1);
      await new Promise(resolve => setTimeout(resolve, 20));
      if (fail) throw new Error("Retry fixture");
    }} />
    </I18nProvider></StrictMode>)); },
  reset() { flushSync(() => root.render(null)); },
};
Object.assign(window, { motionFixture: api });
