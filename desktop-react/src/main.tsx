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
import "./components/sidebar-recent-polish.css";
import "./components/inline-thinking-orb.css";
import "./components/composer-stability.css";
import "./typography-scale.css";
import "./components/model-card-alignment-fix.css";
import "./components/settings-simple.css";
import "./components/computer-use-hud.css";

try {
  const desktop = JSON.parse(window.localStorage.getItem("loom.settings.desktop.v2") || "{}");
  const legacy = JSON.parse(window.localStorage.getItem("loom.settings.generalUi") || "{}");
  const appearance = desktop?.appearance ?? {};

  const scaleValue = appearance.scale === "110" || appearance.scale === "120"
    ? appearance.scale
    : legacy?.scale === "110" || legacy?.scale === "120"
      ? legacy.scale
      : "100";
  const reducedMotion = typeof appearance.reducedMotion === "boolean"
    ? appearance.reducedMotion
    : legacy?.reducedMotion === true;
  const density = appearance.density === "compact" ? "compact" : "comfortable";
  const codeFont = typeof appearance.codeFont === "string" && appearance.codeFont.trim()
    ? appearance.codeFont.trim()
    : "system";
  const codeFontSize = Number.isInteger(appearance.codeFontSize)
    && appearance.codeFontSize >= 10
    && appearance.codeFontSize <= 18
    ? appearance.codeFontSize
    : 12;

  document.documentElement.style.setProperty("zoom", String(Number(scaleValue) / 100));
  document.documentElement.dataset.loomReducedMotion = String(reducedMotion);
  document.documentElement.dataset.loomDensity = density;
  const mono = codeFont === "system"
    ? "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace"
    : `${codeFont}, ui-monospace, monospace`;
  document.documentElement.style.setProperty("--loom-code-font", mono);
  document.documentElement.style.setProperty("--loom-code-font-size", `${codeFontSize}px`);
} catch {
  document.documentElement.style.setProperty("zoom", "1");
  document.documentElement.dataset.loomReducedMotion = "false";
  document.documentElement.dataset.loomDensity = "comfortable";
  document.documentElement.style.setProperty(
    "--loom-code-font",
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace",
  );
  document.documentElement.style.setProperty("--loom-code-font-size", "12px");
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
