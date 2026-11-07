/** The Settings › Phone page over a scripted Host. window.__pair.claim() plays the phone scanning the code. */
import { createRoot } from "react-dom/client";
import { I18nProvider } from "../../src/i18n";
import { applyThemePreference } from "../../src/theme";
import "../../src/renderer-styles";
import { MobilePairingPanel } from "../../src/components/MobilePairingPanel";

window.localStorage.setItem("loom.settings.language", "zh-CN");
applyThemePreference("dark", { animate: false, syncNative: false });

const pair = { state: "waiting", decisions: [] as boolean[], claim() { this.state = "claimed"; } };
Reflect.set(window, "__pair", pair);
let phones = [{ id: "s1", name: "HUAWEI Mate 50", created_at: 1_794_000_000, last_used_at: 1_794_010_000 }];
Reflect.set(window, "loom", {
  mobilePairStart: async () => ({ pairId: "p1", expiresIn: 180, code: "loom://pair?v=1&t=loom_mpair_0123456789abcdef&d=desk-1&n=Desk" }),
  mobilePairStatus: async () => ({ state: pair.state, phoneName: "HUAWEI Mate 50", confirmCode: "482913" }),
  mobilePairDecide: async (_id: string, approve: boolean) => { pair.decisions.push(approve); pair.state = "waiting"; },
  mobileDevices: async () => ({ devices: phones }),
  mobileDeviceRevoke: async (id: string) => { phones = phones.filter((phone) => phone.id !== id); },
});

createRoot(document.getElementById("root")!).render(<I18nProvider><div style={{ padding: 24 }}><MobilePairingPanel /></div></I18nProvider>);
