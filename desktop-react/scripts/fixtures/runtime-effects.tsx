import { createRoot } from "react-dom/client";
import { I18nProvider } from "../../src/i18n";
import { LanguageSettingsDock } from "../../src/components/LanguageSettingsDock";
import { PortalLiquidCursor } from "../../src/components/PortalLiquidCursor";

createRoot(document.getElementById("root")!).render(<I18nProvider>
  <div className="settings-shell" style={{ display: "none" }}>
    <div className="general-overview-card" />
    {Array.from({ length: 2000 }, (_, index) => <div key={index}>Settings</div>)}
    <span id="dynamic-setting">General</span>
  </div>
  <LanguageSettingsDock />
  <div className="loom-portal-page" style={{ position: "fixed", inset: 0, background: "#eee" }}>
    <button className="loom-primary-action" style={{ position: "absolute", left: 200, top: 200 }}>Sign in</button>
    <PortalLiquidCursor />
  </div>
</I18nProvider>);
