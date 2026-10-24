import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { ModelsSettingsPanel } from "../../src/components/ModelsSettingsPanel";
import { I18nProvider } from "../../src/i18n";
import type { ModelProfile, ModelSnapshot } from "../../src/types/loom";
import "../../src/renderer-styles";
import "../../src/components/settings-refined.css";
import "../../src/components/settings-system.css";
const params = new URLSearchParams(location.search);
const theme = params.get("theme") || "dark";
document.documentElement.dataset.loomTheme = theme;
localStorage.setItem("loom.settings.language", params.get("lang") || "en");
const profile = (name: string, groupId: string, groupName: string): ModelProfile => ({ selection: `${groupId}:${name}`, id: name, name, model: name, groupId, groupName, kind: "builtin", adapter: "openai-compatible", baseUrl: "https://gateway.example/v1", configured: true, available: true, vision: true });
const profiles: ModelProfile[] = [profile("deepseek-flash", "deepseek", "DeepSeek"), { ...profile("deepseek-pro", "deepseek", "DeepSeek"), available: false, statusMessage: "Provider retired this model. Refresh to check availability." }, profile("gpt-example", "openai", "OpenAI"), { ...profile("Personal model", "personal", "Personal"), kind: "saved", selection: "saved:personal", baseUrl: "https://personal.example/v1" }];
let snapshot: ModelSnapshot = { primary: profiles[0], current: { ...profiles[0], provider: "deepseek" }, profiles, activeModelId: profiles[0].id, recentModels: [] };
const result = () => ({ models: snapshot });
Object.assign(window, { loom: {
  listModels: async () => snapshot,
  switchModelProfile: async (selection: string) => { const p = profiles.find(p => p.selection === selection)!; snapshot = { ...snapshot, current: { ...p, provider: p.groupId || p.adapter } }; return result(); },
  testModel: async () => ({ modelListed: true, latencyMs: 42, discoveredModels: 2, capabilities: { reasoning: true } }),
  addModel: async () => result(), updateModel: async () => snapshot, deleteModel: async () => result(),
} });
function App() {
  const [state, setState] = useState(snapshot);
  return <I18nProvider><div className="settings-host" style={{ position: "static", minHeight: "100vh", background: "var(--sr-page)", padding: 32, boxSizing: "border-box" }}><div className="settings-shell settings-refined" style={{ display: "block", position: "static", width: "100%", height: "auto", border: 0 }}><ModelsSettingsPanel initialSnapshot={state} running={params.has("running")} onSnapshot={setState} /></div></div></I18nProvider>;
}
createRoot(document.getElementById("root")!).render(<App />);
