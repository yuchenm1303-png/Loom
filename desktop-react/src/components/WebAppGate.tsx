import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { AccountDialog } from "./AccountDialog";
import { RemoteControlSurface } from "./RemoteDevicesPanel";
import { useAccount } from "../state/useAccount";
import { useI18n } from "../i18n";
import {
  isLoomWebRuntime,
  selectedWebDeviceId,
  webExecutionMode,
  type WebDeviceStatus,
} from "../webBridge";

type HostState = "idle" | "checking" | "online" | "offline" | "unbound";

const COPY = {
  en: {
    signIn: "Sign in to use Loom on this computer. Loom Web will not silently connect to another computer on your account.",
    connectLocal: "Connect Loom on this computer",
    connectingLocal: "Connecting to this computer…",
    localOffline: "Loom on this computer is offline",
    connectRemote: "Connecting to remote computer…",
    remoteOffline: "Remote computer is offline",
    localSetup: "Open Loom on this computer, then choose “Open Loom Web” from the Loom tray once. This browser will remember this computer.",
    localConnecting: "Connecting this browser to the Loom Host running on this computer.",
    localOfflineHint: "Start Loom on this computer. Its Host can stay in the background after the Desktop window is closed, and this page will reconnect automatically.",
    remoteConnecting: "Connecting to the computer you explicitly selected in Loom Remote.",
    remoteOfflineHint: "That remote computer is unavailable right now. Choose another computer or return to this computer.",
    remoteDevices: "Remote devices",
  },
  zh: {
    signIn: "登录后使用当前电脑上的 Loom。Loom Web 不会自动连接到你账号里的其他电脑。",
    connectLocal: "连接当前电脑上的 Loom",
    connectingLocal: "正在连接当前电脑…",
    localOffline: "当前电脑上的 Loom 已离线",
    connectRemote: "正在连接远程电脑…",
    remoteOffline: "远程电脑已离线",
    localSetup: "在当前电脑打开 Loom，然后从托盘选择一次「Open Loom Web」。这个浏览器会记住当前电脑。",
    localConnecting: "正在连接这台电脑上运行的 Loom Host。",
    localOfflineHint: "启动当前电脑上的 Loom。关闭桌面窗口后 Host 仍可在后台运行，网页会自动重连。",
    remoteConnecting: "正在连接你在 Loom Remote 中明确选择的电脑。",
    remoteOfflineHint: "这台远程电脑当前不可用。你可以选择另一台电脑，或返回当前电脑。",
    remoteDevices: "远程设备",
  },
} as const;

export function WebAppGate({ children }: { children: ReactNode }) {
  const account = useAccount();
  const { language } = useI18n();
  const copy = COPY[language === "zh-CN" ? "zh" : "en"];
  const web = isLoomWebRuntime();
  const selectedDeviceId = web ? selectedWebDeviceId() : "";
  const remoteMode = web ? webExecutionMode() === "remote" : false;
  const [hostState, setHostState] = useState<HostState>("idle");
  const [hostError, setHostError] = useState("");
  const [selectedDeviceName, setSelectedDeviceName] = useState("");
  const [remoteOpen, setRemoteOpen] = useState(false);

  useEffect(() => {
    if (!web) return;
    const refresh = () => void account.refresh();
    window.addEventListener("loom:web-auth-changed", refresh);
    return () => window.removeEventListener("loom:web-auth-changed", refresh);
  }, [account.refresh, web]);

  useEffect(() => {
    if (!web) return;
    const openRemote = () => setRemoteOpen(true);
    window.addEventListener("loom:web-open-remote", openRemote);
    return () => window.removeEventListener("loom:web-open-remote", openRemote);
  }, [web]);

  useEffect(() => {
    if (!web || !account.ready || !account.account.authenticated || !account.account.user) {
      setHostState("idle");
      setHostError("");
      return;
    }
    if (!selectedDeviceId) {
      setHostState("unbound");
      setHostError("");
      return;
    }

    let cancelled = false;
    let connecting = false;

    const connectHost = async () => {
      if (connecting) return;
      connecting = true;
      if (!cancelled) setHostState("checking");
      try {
        await window.loom.connect();
        if (!cancelled) {
          setHostState("online");
          setHostError("");
        }
      } catch (cause) {
        if (!cancelled) {
          const error = cause as Error & { code?: string };
          setHostState(error.code === "HOST_NOT_SELECTED" ? "unbound" : "offline");
          setHostError(error.message || String(cause));
        }
      } finally {
        connecting = false;
      }
    };

    const onDeviceStatus = (event: Event) => {
      const detail = (event as CustomEvent<WebDeviceStatus>).detail;
      if (detail?.selectedDeviceId && detail.selectedDeviceId !== selectedDeviceId) return;
      setSelectedDeviceName(String(detail?.device?.name || ""));
      if (!detail?.online) {
        setHostState("offline");
        setHostError(remoteMode ? copy.remoteOffline : copy.localOffline);
        return;
      }
      void connectHost();
    };

    window.addEventListener("loom:web-device-status", onDeviceStatus);
    void connectHost();
    return () => {
      cancelled = true;
      window.removeEventListener("loom:web-device-status", onDeviceStatus);
    };
  }, [account.account.authenticated, account.account.user, account.ready, copy.localOffline, copy.remoteOffline, remoteMode, selectedDeviceId, web]);

  if (!web) return children;

  const remoteSurface = <RemoteControlSurface open={remoteOpen} onClose={() => setRemoteOpen(false)} />;

  if (!account.ready || !account.account.authenticated || !account.account.user) {
    return (
      <>
        <main className="boot-error" aria-label="Loom Web sign in">
          <section className="boot-error-card">
            <div className="brand-mark large" aria-hidden="true">L</div>
            <h1>Loom Web</h1>
            <p>{copy.signIn}</p>
          </section>
        </main>
        <AccountDialog
          open
          account={account.account}
          ready={account.ready}
          busy={account.busy}
          error={account.error}
          onClose={() => undefined}
          onClearError={account.clearError}
          onRetry={account.refresh}
          onLogin={account.login}
          onRegister={account.register}
          onLogout={account.logout}
        />
      </>
    );
  }

  if (hostState !== "online") {
    const unbound = hostState === "unbound";
    const checking = hostState === "checking" || hostState === "idle";
    const heading = remoteMode
      ? checking ? copy.connectRemote : copy.remoteOffline
      : unbound ? copy.connectLocal : checking ? copy.connectingLocal : copy.localOffline;
    const body = remoteMode
      ? checking ? copy.remoteConnecting : copy.remoteOfflineHint
      : unbound ? copy.localSetup : checking ? copy.localConnecting : copy.localOfflineHint;

    return (
      <>
        <main className="boot-error" aria-label="Loom Host connection">
          <section className="boot-error-card">
            <div className="brand-mark large" aria-hidden="true">L</div>
            <h1>{heading}</h1>
            {remoteMode && selectedDeviceName ? <div className="web-host-selected-device">{selectedDeviceName}</div> : null}
            <p>{body}</p>
            {!checking && hostError ? <p className="boot-error-detail">{hostError}</p> : null}
            <div className="web-host-gate-actions">
              <button type="button" className="button secondary" onClick={() => setRemoteOpen(true)}>{copy.remoteDevices}</button>
            </div>
          </section>
        </main>
        {remoteSurface}
      </>
    );
  }

  return <>{children}{remoteSurface}</>;
}
