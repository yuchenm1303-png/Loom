/** Weave mode against a live Host (scripts/weave_live.py): the real ProjectMode component,
 *  with window.loom.call forwarded to the Host's token-protected 127.0.0.1 bridge.
 *  /scripts/fixtures/weave-live.html?bridge=http://127.0.0.1:PORT&token=TOKEN&thread=ID&model=NAME[&theme=light]
 *  Only the project calls exist on the bridge; this page makes no other request.
 */
import { createRoot } from "react-dom/client";
import { I18nProvider } from "../../src/i18n";
import { ProjectMode } from "../../src/components/ProjectMode";
import { ProjectModeToggle } from "../../src/components/ProjectModeToggle";
import { applyThemePreference } from "../../src/theme";
import "../../src/renderer-styles";

const params = new URLSearchParams(location.search);
localStorage.setItem("loom.settings.language", params.get("lang") || "zh-CN");
applyThemePreference(params.get("theme") === "light" ? "light" : "dark", { animate: false, syncNative: false });
const bridge = params.get("bridge") || "";
const token = params.get("token") || "";

Object.assign(window, { loom: { async call(method: string, values: Record<string, unknown> = {}) {
  const response = await fetch(`${bridge}/call`, { method: "POST", headers: { "content-type": "application/json", "x-weave-token": token },
    body: JSON.stringify({ method, params: values }) });
  const body = await response.json().catch(() => ({ error: `HTTP ${response.status}` }));
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
} } });

createRoot(document.getElementById("root")!).render(
  <I18nProvider>
    <div style={{ display: "grid", gridTemplateRows: "60px 1fr", height: "100vh", background: "var(--loom-workspace-canvas)", color: "var(--loom-workspace-ink)" }}>
      <header style={{ display: "flex", alignItems: "center", gap: 12, padding: "0 20px" }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>ledger</span>
        <span style={{ marginLeft: "auto" }}><ProjectModeToggle value="project" onChange={() => undefined} /></span>
      </header>
      <div className="project-mode-stage" style={{ gridRow: "2" }}>
        <ProjectMode threadId={params.get("thread") || ""} workspace="ledger" model={params.get("model") || ""} />
      </div>
    </div>
  </I18nProvider>,
);
