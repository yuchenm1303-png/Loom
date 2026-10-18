import React, { useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { ModelPanel } from "../../src/components/ModelPanel";
import { I18nProvider } from "../../src/i18n";
import type { ModelSnapshot } from "../../src/types/loom";
import "../../src/renderer-styles";

const noop = () => {};
function App() {
  const [updated, setUpdated] = useState(false);
  const profile = (model: string) => ({ selection: `builtin:opencode-go:${model}`, id: model, kind: "builtin" as const,
    name: model === "beam-free" ? "Beam Free" : model, model, groupId: "opencode-go", groupName: "OpenCode Go",
    family: "Other", adapter: "opencode-go", baseUrl: "https://account.smirel.com/model/v1/opencode-go",
    authMode: "loom-account" as const, configured: true, available: true });
  const profiles = [profile("omen-alpha"), ...Array.from({ length: 8 }, (_, i) => profile(`fixture-${i}`)),
    { ...profile("deepseek-flash"), available: false, catalogState: "retired" as const, accessState: "not-listed" as const,
      statusMessage: "Model is not in the confirmed provider catalog" }, ...(updated ? [profile("beam-free")] : [])];
  const snapshot: ModelSnapshot = { primary: profiles[0], current: { ...profiles[0], provider: "opencode-go" }, profiles,
    activeModelId: profiles[0].id, recentModels: [], policySync: { state: "fresh", checkedAt: Date.now() },
    catalogSync: { providers: [{ groupId: "opencode-go", state: updated ? "synced" : "stale", lastSuccess: 1792163104,
      lastAttempt: 1792163404, message: updated ? undefined : "This provider's catalog could not be validated; its previous directory is retained." }] } };
  const refreshes = useRef(0);
  const refresh = () => { if (++refreshes.current > 1) setUpdated(true); };
  return <I18nProvider><div style={{ width: 620, height: 800, margin: 24 }}>
    <ModelPanel snapshot={snapshot} accountAuthenticated onSwitchProfile={selection => {
      Object.assign(window, { selectedCatalogModel: selection });
    }} onSwitchCurrent={noop} onConfigureProvider={noop} onAddModel={noop} onDeleteModel={noop}
      onReasoningChange={noop} onClose={noop} onRefresh={refresh} />
  </div></I18nProvider>;
}
createRoot(document.getElementById("root")!).render(<App />);
