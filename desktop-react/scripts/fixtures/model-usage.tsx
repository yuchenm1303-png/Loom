import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { Composer } from "../../src/components/ComposerBase";
import { I18nProvider } from "../../src/i18n";
import type { ModelSnapshot } from "../../src/types/loom";
import type { ModelUsage } from "../../src/types/modelUsage";
import "../../src/styles.css";

const fixture = { delay: false, fail: false, pending: [] as Array<() => void>, calls: [] as string[] };
Object.assign(window, { usageFixture: fixture });
Object.assign(window, { loom: { queryModelUsage: async (selection: string): Promise<ModelUsage> => {
  fixture.calls.push(selection);
  if (fixture.delay) {
    fixture.delay = false;
    await new Promise<void>(resolve => fixture.pending.push(resolve));
  }
  if (fixture.fail) { fixture.fail = false; return { status: "error", provider: "MiniMax", checkedAt: Date.now(), windows: [], balances: [], message: "查询失败" }; }
  return { status: selection === "unsupported" ? "unsupported" : "ok", provider: selection, checkedAt: Date.now(),
    windows: selection === "minimax" ? [{ label: "5h", remainingPercent: 61 }, { label: "Weekly", remainingPercent: 88, resetsAt: Date.now() + 86400000 }] : [],
    balances: selection === "deepseek" ? [{ currency: "CNY", total: "12.3456", toppedUp: "10", granted: "2.3456" }] : [],
    message: selection === "unsupported" ? "此连接暂不支持额度查询" : undefined };
} } });

function App() {
  const [selection, setSelection] = useState("minimax");
  const profile = { selection, id: selection, kind: "builtin", name: selection, adapter: "openai-compatible", baseUrl: "", model: selection, provider: "openai-compatible" };
  const snapshot = { primary: profile, profiles: [profile], current: profile, activeModelId: selection, recentModels: [] } as ModelSnapshot;
  return <I18nProvider><div style={{ padding: 24 }}>
    {["minimax", "deepseek", "unsupported"].map(value => <button key={value} onClick={() => setSelection(value)}>{value}</button>)}
    <div style={{ position: "fixed", bottom: 48, left: 24, right: 24 }}>
      <Composer modelSnapshot={snapshot} onSend={() => {}} onInterrupt={() => {}} />
    </div>
  </div></I18nProvider>;
}
createRoot(document.getElementById("root")!).render(<App />);
