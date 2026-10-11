import React from "react";
import { createRoot } from "react-dom/client";
import { Composer } from "../../src/components/Composer";
import { useLoom } from "../../src/state/useLoomCore";
import { I18nProvider } from "../../src/i18n";
import "../../src/styles.css";

const pending: Array<{ resolve(value: unknown): void; reject(error: Error): void }> = [];
const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
Object.assign(window, { projectSendFixture: { pending, calls } });
const bridge = {
  connect: async () => ({ runtime: {} }),
  listModels: async () => null,
  onNotification: () => () => {},
  call: async (method: string, params: Record<string, unknown> = {}) => {
    calls.push({ method, params });
    if (method === "thread/list") return { threads: [], counts: { active: 0, archived: 0, all: 0 } };
    if (method === "project/list") return { projects: [] };
    if (method === "thread/start") return new Promise((resolve, reject) => pending.push({ resolve, reject }));
    if (method === "turn/start") return { turn: { id: "turn-1" } };
    return {};
  },
};
Object.assign(window, { loom: bridge });
function Fixture() {
  const loom = useLoom();
  return <>
    <button onClick={() => void loom.newThread("C:/project", "project-1")}>Project draft</button>
    <button onClick={() => void loom.newThread()}>Other draft</button>
    <output>{loom.connection}</output>
    <div data-thread={loom.active?.thread.id ?? ""}>{loom.items.map(item => <p key={item.id}>{String(item.text)}</p>)}</div>
    <Composer threadId={loom.active?.thread.id} onSend={loom.send} onInterrupt={loom.interrupt}
      disabled={loom.connection !== "ready"} running={loom.turnActive} accountAuthenticated />
  </>;
}
createRoot(document.getElementById("root")!).render(<I18nProvider><Fixture /></I18nProvider>);
