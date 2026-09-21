import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { BootErrorBoundary } from "./components/BootErrorBoundary";
import { I18nProvider, bootstrapDocumentLanguage } from "./i18n";
import { bootstrapTheme } from "./theme";
import { applyRendererScale, installNativeRendererScaleSync } from "./rendererScale";
import "./styles.css";
import "./shell-fix.css";
import "./components/model-panel-overrides.css";
import "./components/reasoning-control.css";
import "./components/inline-thinking-orb.css";
import "./components/composer-stability.css";
import "./components/composer-attachment-polish.css";
import "./typography-scale.css";
import "./components/model-card-alignment-fix.css";
import "./components/settings-simple.css";
import "./components/settings-icon-alignment.css";
import "./components/computer-use-hud.css";
import "./components/inspector-tabs.css";
import "./components/semantic-colors.css";
import "./components/thread-header-mark-refinement.css";
import "./components/settings-models-polish.css";
import "./components/settings-capabilities-polish.css";
import "./components/runtime-live-feedback.css";
import "./components/review-motion.css";
import "./components/sidebar-clarity-fix.css";
import "./components/renderer-crispness.css";
import "./components/model-core-redesign.css";
import "./theme.css";
import "./components/sidebar-primary-actions-polish.css";
import "./components/permission-popover-polish.css";

const CONVERSATION_WIDTHS: Record<string, string> = {
  focused: "740px",
  balanced: "860px",
  wide: "1040px",
};
const SIDEBAR_WIDTHS: Record<string, string> = {
  compact: "220px",
  standard: "252px",
  wide: "292px",
};
const INSPECTOR_WIDTHS: Record<string, string> = {
  compact: "280px",
  standard: "316px",
  wide: "360px",
};
const MESSAGE_LINE_HEIGHTS: Record<string, string> = {
  compact: "1.54",
  comfortable: "1.72",
  relaxed: "1.90",
};
const CODE_LINE_HEIGHTS: Record<string, string> = {
  compact: "1.46",
  comfortable: "1.62",
  relaxed: "1.80",
};

try {
  const desktop = JSON.parse(window.localStorage.getItem("loom.settings.desktop.v2") || "{}");
  const legacy = JSON.parse(window.localStorage.getItem("loom.settings.generalUi") || "{}");
  const appearance = desktop?.appearance ?? {};

  const validScales = new Set(["90", "100", "110", "120", "130"]);
  const scaleValue = validScales.has(appearance.scale)
    ? appearance.scale
    : validScales.has(legacy?.scale)
      ? legacy.scale
      : "100";
  const reducedMotion = typeof appearance.reducedMotion === "boolean"
    ? appearance.reducedMotion
    : legacy?.reducedMotion === true;
  const density = ["compact", "comfortable", "spacious"].includes(appearance.density)
    ? appearance.density
    : "comfortable";
  const conversationWidth = CONVERSATION_WIDTHS[appearance.conversationWidth]
    ? appearance.conversationWidth
    : "balanced";
  const sidebarWidth = SIDEBAR_WIDTHS[appearance.sidebarWidth]
    ? appearance.sidebarWidth
    : "standard";
  const inspectorWidth = INSPECTOR_WIDTHS[appearance.inspectorWidth]
    ? appearance.inspectorWidth
    : "standard";
  const chatFontSize = Number.isInteger(appearance.chatFontSize)
    && appearance.chatFontSize >= 11
    && appearance.chatFontSize <= 18
    ? appearance.chatFontSize
    : 13;
  const messageLineHeight = MESSAGE_LINE_HEIGHTS[appearance.messageLineHeight]
    ? appearance.messageLineHeight
    : "comfortable";
  const ambientEffects = typeof appearance.ambientEffects === "boolean" ? appearance.ambientEffects : true;
  const codeFont = typeof appearance.codeFont === "string" && appearance.codeFont.trim()
    ? appearance.codeFont.trim()
    : "system";
  const codeFontSize = Number.isInteger(appearance.codeFontSize)
    && appearance.codeFontSize >= 10
    && appearance.codeFontSize <= 18
    ? appearance.codeFontSize
    : 12;
  const codeLineHeight = CODE_LINE_HEIGHTS[appearance.codeLineHeight]
    ? appearance.codeLineHeight
    : "comfortable";
  const codeWrap = appearance.codeWrap === true;

  // Never put the initial renderer through CSS zoom. On Windows that can leave
  // text and thin geometry in a softened compositor raster even after the CSS
  // property is removed. Apply the persisted scale directly through Chromium.
  applyRendererScale(Number(scaleValue) / 100);
  document.documentElement.dataset.loomReducedMotion = String(reducedMotion);
  document.documentElement.dataset.loomDensity = density;
  document.documentElement.dataset.loomAmbientEffects = String(ambientEffects);
  document.documentElement.dataset.loomCodeWrap = String(codeWrap);
  document.documentElement.style.setProperty("--content-width", CONVERSATION_WIDTHS[conversationWidth]);
  document.documentElement.style.setProperty("--sidebar-width", SIDEBAR_WIDTHS[sidebarWidth]);
  document.documentElement.style.setProperty("--inspector-width", INSPECTOR_WIDTHS[inspectorWidth]);
  document.documentElement.style.setProperty("--loom-chat-font-size", `${chatFontSize}px`);
  document.documentElement.style.setProperty("--loom-message-line-height", MESSAGE_LINE_HEIGHTS[messageLineHeight]);
  const mono = codeFont === "system"
    ? "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace"
    : `${codeFont}, ui-monospace, monospace`;
  document.documentElement.style.setProperty("--loom-code-font", mono);
  document.documentElement.style.setProperty("--loom-code-font-size", `${codeFontSize}px`);
  document.documentElement.style.setProperty("--loom-code-line-height", CODE_LINE_HEIGHTS[codeLineHeight]);
} catch {
  applyRendererScale(1);
  document.documentElement.dataset.loomReducedMotion = "false";
  document.documentElement.dataset.loomDensity = "comfortable";
  document.documentElement.dataset.loomAmbientEffects = "true";
  document.documentElement.dataset.loomCodeWrap = "false";
  document.documentElement.style.setProperty("--content-width", "860px");
  document.documentElement.style.setProperty("--sidebar-width", "252px");
  document.documentElement.style.setProperty("--inspector-width", "316px");
  document.documentElement.style.setProperty("--loom-chat-font-size", "13px");
  document.documentElement.style.setProperty("--loom-message-line-height", "1.72");
  document.documentElement.style.setProperty(
    "--loom-code-font",
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace",
  );
  document.documentElement.style.setProperty("--loom-code-font-size", "12px");
  document.documentElement.style.setProperty("--loom-code-line-height", "1.62");
}

bootstrapTheme();
installNativeRendererScaleSync();
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
