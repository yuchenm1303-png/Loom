import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ProjectFlow, type FlowData } from "../../src/components/ProjectFlow";
import { I18nProvider } from "../../src/i18n";
import "../../src/renderer-styles";

const decision = { title: "下一步研究方向", multiple: new URLSearchParams(location.search).has("multiple"),
  options: [{ id: "calibration", title: "先做高 SNR LLR 校准" }, { id: "pilot_density", title: "先做导频密度对比" }] };
const content = "研究已经完成。\n\n```loom-decision\n" + JSON.stringify(decision) + "\n```";
const base: FlowData = { phase: "draft", objective: "", narration: { summary: "", text: "" }, milestone: 0, state: "paused", model: "test",
  repository: { name: "test", commit: "", subject: "", branch: "main" }, scope: [], knowledge: { checks: 0, notes: 0, latest: "" },
  tasks: [], budget: { tokens: 0, tokenLimit: 100000 }, workers: 1, maxWorkers: 2, controls: { pause: false, resume: false, cancel: false }, agents: [] };
function Fixture() {
  const [conversation, setConversation] = useState<NonNullable<FlowData["conversation"]>>([{ id: "answer", role: "assistant", text: content }]);
  const [chatting, setChatting] = useState(false);
  const [version, setVersion] = useState(0);
  Object.assign(window, { decisionFixture: {
    set: (text: string, live = false) => { setConversation([{ id: "answer", role: "assistant", text, live, streaming: live }]); setChatting(live); },
    restore: () => setVersion(v => v + 1), content,
  } });
  return <div style={{ height: "100vh" }}><ProjectFlow key={version} data={{ ...base, conversation, chatting }} actions={{
    onChat: async (text, attachments, tokens) => {
      const state = window as unknown as { sent?: unknown[]; fail?: boolean };
      state.sent = [text, attachments, tokens];
      if (state.fail) throw new Error("Send failed");
      setConversation(items => [...items, { id: crypto.randomUUID(), role: "user", text }]);
    },
  }} /></div>;
}
createRoot(document.getElementById("root")!).render(<I18nProvider><Fixture /></I18nProvider>);
