import { Laptop, Monitor, MonitorSmartphone, RotateCw, ShieldCheck, Wifi, WifiOff, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import {
  activateLocalWebDevice,
  activateWebRemoteDevice,
  currentWebDeviceStatus,
  getWebDeviceStatus,
  localWebDeviceId,
  selectedWebDeviceId,
  webExecutionMode,
  type WebDeviceStatus,
  type WebRelayDevice,
} from "../webBridge";
import "./remote-devices.css";

type KnownDevice = { id: string; name: string; platform: string; version: string; lastSeenAt: number };
const KNOWN_DEVICES_STORAGE_KEY = "loom.web.knownDevices";

const COPY = {
  en: {
    title: "Loom Remote", subtitle: "Control another computer only when you choose it.",
    thisComputer: "This computer", thisComputerHint: "Loom's default execution environment",
    linked: "Linked to this browser", notLinked: "Not linked in this browser",
    online: "Online", offline: "Offline", useThis: "Use this computer", usingThis: "Using this computer",
    otherComputers: "Other computers", otherHint: "Remote control is always explicit. Loom never falls back to another computer.",
    noDevices: "No other computers have been seen from this browser yet.",
    noDevicesHint: "Sign in to Loom on another computer and keep its Host running to make it available here.",
    connect: "Connect", connected: "Connected", refresh: "Refresh", loading: "Loading devices…", close: "Close",
    lastSeen: "Last seen", now: "just now", remote: "Remote", backLocal: "Back to this computer",
    security: "Remote sessions stay scoped to this browser session. A new browser session starts Local again.",
    selectedOffline: "Selected remote computer",
    localSetup: "Open Loom on this computer and choose “Open Loom Web” from the tray once to link it.",
  },
  zh: {
    title: "Loom Remote", subtitle: "只有你明确选择后，Loom 才会控制另一台电脑。",
    thisComputer: "当前电脑", thisComputerHint: "Loom 默认执行环境",
    linked: "已绑定到此浏览器", notLinked: "此浏览器尚未绑定",
    online: "在线", offline: "离线", useThis: "切回当前电脑", usingThis: "正在使用当前电脑",
    otherComputers: "其他电脑", otherHint: "远程控制始终需要你主动选择，Loom 不会自动回退到其他电脑。",
    noDevices: "这个浏览器还没有发现其他电脑。",
    noDevicesHint: "在另一台电脑登录 Loom 并保持 Host 运行后，它会出现在这里。",
    connect: "连接", connected: "已连接", refresh: "刷新", loading: "正在加载设备…", close: "关闭",
    lastSeen: "最近在线", now: "刚刚", remote: "远程", backLocal: "返回当前电脑",
    security: "远程选择只在当前浏览器会话中生效。新开浏览器会话仍默认回到 Local。",
    selectedOffline: "已选择的远程电脑",
    localSetup: "在当前电脑打开 Loom，然后从托盘选择一次「Open Loom Web」即可完成本机绑定。",
  },
} as const;

function readKnownDevices(): Record<string, KnownDevice> {
  try {
    const raw = window.localStorage.getItem(KNOWN_DEVICES_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed as Record<string, KnownDevice> : {};
  } catch { return {}; }
}

function writeKnownDevices(devices: Record<string, KnownDevice>): void {
  try { window.localStorage.setItem(KNOWN_DEVICES_STORAGE_KEY, JSON.stringify(devices)); } catch {}
}

function normalizedDevice(device: WebRelayDevice, seenAt: number): KnownDevice | null {
  const id = String(device.id || "").trim();
  if (!id) return null;
  return {
    id,
    name: String(device.name || "").trim() || "Loom computer",
    platform: String(device.platform || "").trim(),
    version: String(device.version || "").trim(),
    lastSeenAt: seenAt,
  };
}

function platformIcon(platform: string) {
  const folded = platform.toLowerCase();
  if (folded.includes("win")) return <Monitor size={19} strokeWidth={1.75} />;
  if (folded.includes("darwin") || folded.includes("mac")) return <Laptop size={19} strokeWidth={1.75} />;
  return <MonitorSmartphone size={19} strokeWidth={1.75} />;
}

function relativeSeen(timestamp: number, nowLabel: string): string {
  if (!timestamp) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return nowLabel;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

export function RemoteControlSurface({ open, onClose }: { open: boolean; onClose(): void }) {
  const { language } = useI18n();
  const copy = COPY[language === "zh-CN" ? "zh" : "en"];
  const [status, setStatus] = useState<WebDeviceStatus | null>(() => currentWebDeviceStatus());
  const [known, setKnown] = useState<Record<string, KnownDevice>>(() => readKnownDevices());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const mode = webExecutionMode();
  const localId = localWebDeviceId();
  const selectedId = selectedWebDeviceId();

  const syncStatus = (next: WebDeviceStatus) => {
    setStatus(next);
    const seenAt = Date.now();
    setKnown((current) => {
      const merged = { ...current };
      for (const device of next.devices || []) {
        const normalized = normalizedDevice(device, seenAt);
        if (normalized) merged[normalized.id] = normalized;
      }
      if (next.device) {
        const normalized = normalizedDevice(next.device, seenAt);
        if (normalized) merged[normalized.id] = normalized;
      }
      writeKnownDevices(merged);
      return merged;
    });
  };

  const refresh = async () => {
    setLoading(true); setError("");
    try { syncStatus(await getWebDeviceStatus()); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setLoading(false); }
  };

  useEffect(() => {
    const onStatus = (event: Event) => syncStatus((event as CustomEvent<WebDeviceStatus>).detail);
    window.addEventListener("loom:web-device-status", onStatus);
    if (open || mode === "remote") void refresh();
    return () => window.removeEventListener("loom:web-device-status", onStatus);
  }, [open, mode]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const onlineIds = useMemo(() => new Set((status?.devices || []).map((device) => String(device.id || "")).filter(Boolean)), [status]);
  const localDevice = localId ? known[localId] : undefined;
  const remoteDevices = useMemo(() => {
    const entries = Object.values(known).filter((device) => device.id && device.id !== localId).sort((a, b) => {
      const aOnline = onlineIds.has(a.id) ? 1 : 0;
      const bOnline = onlineIds.has(b.id) ? 1 : 0;
      return aOnline !== bOnline ? bOnline - aOnline : b.lastSeenAt - a.lastSeenAt;
    });
    if (mode === "remote" && selectedId && selectedId !== localId && !entries.some((device) => device.id === selectedId)) {
      entries.unshift({ id: selectedId, name: copy.selectedOffline, platform: "", version: "", lastSeenAt: 0 });
    }
    return entries;
  }, [copy.selectedOffline, known, localId, mode, onlineIds, selectedId]);
  const selectedDevice = selectedId ? known[selectedId] : undefined;
  const selectedOnline = Boolean(selectedId && onlineIds.has(selectedId));

  const panel = open ? createPortal(
    <div className="remote-devices-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="remote-devices-panel" role="dialog" aria-modal="true" aria-label={copy.title}>
        <header className="remote-devices-header">
          <div className="remote-devices-heading">
            <span className="remote-devices-heading-icon"><MonitorSmartphone size={20} strokeWidth={1.75} /></span>
            <div><h2>{copy.title}</h2><p>{copy.subtitle}</p></div>
          </div>
          <div className="remote-devices-header-actions">
            <button type="button" onClick={() => void refresh()} title={copy.refresh} aria-label={copy.refresh} disabled={loading}><RotateCw size={15.5} className={loading ? "is-spinning" : ""} /></button>
            <button type="button" onClick={onClose} title={copy.close} aria-label={copy.close}><X size={16} /></button>
          </div>
        </header>

        <div className="remote-devices-scroll">
          <section className="remote-device-section">
            <div className="remote-device-section-label"><span>{copy.thisComputer}</span><small>{copy.thisComputerHint}</small></div>
            <article className={`remote-device-card local ${mode === "local" ? "is-selected" : ""}`}>
              <div className="remote-device-icon">{platformIcon(localDevice?.platform || "win32")}</div>
              <div className="remote-device-copy">
                <div className="remote-device-title-row">
                  <strong>{localDevice?.name || copy.thisComputer}</strong>
                  <span className={`remote-device-state ${localId && onlineIds.has(localId) ? "online" : "offline"}`}>
                    {localId && onlineIds.has(localId) ? <Wifi size={12.5} /> : <WifiOff size={12.5} />}
                    {localId && onlineIds.has(localId) ? copy.online : copy.offline}
                  </span>
                </div>
                <p>{localId ? copy.linked : copy.notLinked}</p>
                {!localId ? <small className="remote-device-helper">{copy.localSetup}</small> : null}
              </div>
              <button type="button" className="remote-device-action" disabled={mode === "local" || !localId} onClick={() => activateLocalWebDevice()}>
                {mode === "local" ? copy.usingThis : copy.useThis}
              </button>
            </article>
          </section>

          <section className="remote-device-section">
            <div className="remote-device-section-label"><span>{copy.otherComputers}</span><small>{copy.otherHint}</small></div>
            {loading && !remoteDevices.length ? <div className="remote-devices-empty">{copy.loading}</div> : null}
            {!loading && !remoteDevices.length ? (
              <div className="remote-devices-empty"><MonitorSmartphone size={21} strokeWidth={1.5} /><strong>{copy.noDevices}</strong><span>{copy.noDevicesHint}</span></div>
            ) : null}
            <div className="remote-device-list">
              {remoteDevices.map((device) => {
                const online = onlineIds.has(device.id);
                const selected = mode === "remote" && selectedId === device.id;
                return (
                  <article className={`remote-device-card ${selected ? "is-selected is-remote" : ""}`} key={device.id}>
                    <div className="remote-device-icon">{platformIcon(device.platform)}</div>
                    <div className="remote-device-copy">
                      <div className="remote-device-title-row"><strong>{device.name}</strong><span className={`remote-device-state ${online ? "online" : "offline"}`}>{online ? <Wifi size={12.5} /> : <WifiOff size={12.5} />}{online ? copy.online : copy.offline}</span></div>
                      <p>{[device.platform, device.version ? `Loom ${device.version}` : ""].filter(Boolean).join(" · ") || copy.remote}</p>
                      {!online && device.lastSeenAt ? <small className="remote-device-helper">{copy.lastSeen} · {relativeSeen(device.lastSeenAt, copy.now)}</small> : null}
                    </div>
                    <button type="button" className="remote-device-action" disabled={!online || selected} onClick={() => activateWebRemoteDevice(device.id)}>{selected ? copy.connected : online ? copy.connect : copy.offline}</button>
                  </article>
                );
              })}
            </div>
          </section>
          {error ? <div className="remote-devices-error">{error}</div> : null}
        </div>
        <footer className="remote-devices-footer"><ShieldCheck size={15} strokeWidth={1.7} /><span>{copy.security}</span></footer>
      </section>
    </div>, document.body,
  ) : null;

  return <>
    {mode === "remote" ? (
      <div className={`remote-session-banner ${selectedOnline ? "online" : "offline"}`} role="status">
        <button type="button" className="remote-session-main" onClick={() => window.dispatchEvent(new CustomEvent("loom:web-open-remote"))}>
          <span className="remote-session-dot" aria-hidden="true" /><span className="remote-session-label">{copy.remote}</span><strong>{selectedDevice?.name || copy.selectedOffline}</strong>
        </button>
        <span className="remote-session-divider" />
        <button type="button" className="remote-session-back" onClick={() => activateLocalWebDevice()}>{copy.backLocal}</button>
      </div>
    ) : null}
    {panel}
  </>;
}
