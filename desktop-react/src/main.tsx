import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { BootErrorBoundary } from "./components/BootErrorBoundary";
import { I18nProvider, bootstrapDocumentLanguage } from "./i18n";
import "./styles.css";
import "./shell-fix.css";
import "./components/model-panel-overrides.css";
import "./components/reasoning-control.css";
import "./components/sidebar-brand-polish.css";
import "./components/inline-thinking-orb.css";
import "./components/composer-stability.css";
import "./typography-scale.css";
import "./components/model-card-alignment-fix.css";

try {
  const saved = JSON.parse(window.localStorage.getItem("loom.settings.generalUi") || "{}");
  const scale = saved?.scale === "110" || saved?.scale === "120" ? Number(saved.scale) / 100 : 1;
  document.documentElement.style.setProperty("zoom", String(scale));
  document.documentElement.dataset.loomReducedMotion = String(saved?.reducedMotion === true);
} catch {
  document.documentElement.style.setProperty("zoom", "1");
  document.documentElement.dataset.loomReducedMotion = "false";
}

bootstrapDocumentLanguage();

const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("Loom renderer root element is missing");

createRoot(rootElement).render(
  <StrictMode>
    <BootErrorBoundary>
      <I18nProvider>
        <App />
      </I18nProvider>
    </BootErrorBoundary>
  </StrictMode>,
);
