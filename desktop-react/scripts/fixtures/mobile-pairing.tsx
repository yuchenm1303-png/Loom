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
let phones = [{ id: "s1", name: "HUAWEI Mate 50", created_at: 1_794_000_000, last_used_at: 1_794_010_000, online: true, current: false }];
let history = [{ id: "s0", name: "HUAWEI P40", created_at: 1_790_000_000, removed_at: 1_792_000_000 }];
let computers = [
  { id: "desk-1", name: "书房台式机", platform: "win32", version: "0.1.182", last_seen_at: 1_794_010_000, online: true },
  { id: "laptop", name: "轻薄本", platform: "win32", version: "0.1.170", last_seen_at: 1_793_000_000, online: false },
];
Reflect.set(window, "loom", {
  mobilePairStart: async () => ({ pairId: "p1", expiresIn: 180, code: "loom://pair?v=1&t=loom_mpair_0123456789abcdef&d=desk-1&n=Desk" }),
  mobilePairStatus: async () => ({ state: pair.state, phoneName: "HUAWEI Mate 50", confirmCode: "482913" }),
  mobilePairDecide: async (_id: string, approve: boolean) => { pair.decisions.push(approve); pair.state = "waiting"; },
  accountDevices: async () => ({ phones, history, computers, thisComputer: "desk-1" }),
  mobileDeviceRevoke: async (id: string) => {
    const phone = phones.find((entry) => entry.id === id)!;
    phones = phones.filter((entry) => entry.id !== id);
    history = [{ id, name: phone.name, created_at: phone.created_at, removed_at: 1_794_020_000 }, ...history];
  },
  accountDeviceForget: async (kind: string, id: string) => {
    if (kind === "phone") history = history.filter((entry) => entry.id !== id);
    else computers = computers.filter((entry) => entry.id !== id);
  },
});

createRoot(document.getElementById("root")!).render(<I18nProvider><div style={{ padding: 24 }}><MobilePairingPanel /></div></I18nProvider>);
