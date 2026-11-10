import { createRoot } from "react-dom/client";
import { BootErrorBoundary } from "../components/BootErrorBoundary";
import { I18nProvider, bootstrapDocumentLanguage } from "../i18n";
import { bootstrapTheme } from "../theme";
import "../renderer-styles";
import "./mobile.css";
import { MobileApp } from "./MobileApp";
import { installMobileBridge } from "./mobileBridge";
import { installTouchPress } from "./motion";
import { installRubberBand } from "./rubberBand";

installMobileBridge();
installTouchPress();
installRubberBand();
// The shared transcript takes its language from here; the phone's own screens are Chinese.
try { if (!window.localStorage.getItem("loom.settings.language")) window.localStorage.setItem("loom.settings.language", "zh-CN"); } catch {}
bootstrapDocumentLanguage();
bootstrapTheme();

createRoot(document.getElementById("root")!).render(
  <BootErrorBoundary>
    <I18nProvider>
      <MobileApp />
    </I18nProvider>
  </BootErrorBoundary>,
);
