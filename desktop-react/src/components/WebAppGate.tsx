import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { AccountDialog } from "./AccountDialog";
import { RemoteControlSurface } from "./RemoteDevicesPanel";
import { RemoteSidebarEntry } from "./RemoteSidebarEntry";
import { useI18n } from "../i18n";
import { useAccount } from "../state/useAccount";
import {
  isLoomWebRuntime,
  selectedWebDeviceId,
  webExecutionMode,
  type WebDeviceStatus,
} from "../webBridge";
import "./web-smirel.css";

type HostState = "idle" | "checking" | "online" | "offline" | "unbound";

// Product contract: Other computers are never selected automatically; remote control belongs in Loom Remote.

const COPY = {
  en: {
    signIn: "Sign in to use Loom on this computer. Loom Web will not silently connect to another computer on your account.",
    connectLocal: "Connect Loom on this computer",
    connectingLocal: "Connecting to this computer…",
    localOffline: "Loom on this computer is offline",
    connectRemote: "Connecting to remote computer…",
    remoteOffline: "Remote computer is offline",
    localSetup: "Open Loom on this computer, then choose “Open Loom Web” from the Loom tray once. This browser will remember this computer.",
    localConnecting: "Establishing a secure connection between this browser and your local Loom Host.",
    localOfflineHint: "Start Loom on this computer. The Desktop window may close while Loom Host keeps running in the background.",
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
    localConnecting: "正在建立浏览器与本机 Loom Host 的安全连接。",
    localOfflineHint: "启动当前电脑上的 Loom。Desktop 窗口可以关闭，Loom Host 会继续在后台保持连接。",
    remoteConnecting: "正在连接你在 Loom Remote 中明确选择的电脑。",
    remoteOfflineHint: "这台远程电脑当前不可用。你可以选择另一台电脑，或返回当前电脑。",
    remoteDevices: "远程设备",
  },
} as const;

const SMIREL_LOGO = "/smirel-logo.svg";

function SmirelShell({ children, compact = false }: { children: ReactNode; compact?: boolean }) {
  const { language } = useI18n();
  const zh = language === "zh-CN";

  return (
    <div className={`smirel-web-shell${compact ? " is-compact" : ""}`}>
      <div className="smirel-web-aurora smirel-web-aurora-a" aria-hidden="true" />
      <div className="smirel-web-aurora smirel-web-aurora-b" aria-hidden="true" />
      <div className="smirel-web-grid" aria-hidden="true" />

      <header className="smirel-web-brandbar">
        <a className="smirel-web-brand" href="/" aria-label="Smirel Loom">
          <img src={SMIREL_LOGO} alt="Smirel" />
          <span className="smirel-web-brand-divider" aria-hidden="true" />
          <span className="smirel-web-product">LOOM</span>
        </a>
        <span className="smirel-web-status"><i aria-hidden="true" />{zh ? "安全连接" : "Secure connection"}</span>
      </header>

      <main className="smirel-web-layout">
        <section className="smirel-web-intro" aria-label="Smirel Loom Web">
          <span className="smirel-web-kicker">SMIREL · LOOM WEB</span>
          <h1>
            {zh ? "你的 Loom，" : "Your Loom,"}<br />
            <span>{zh ? "随时从浏览器打开。" : "ready in your browser."}</span>
          </h1>
          <p>
            {zh
              ? "网页只负责安全连接。任务、工具、文件和 Computer Use 仍由你这台电脑上的 Loom Host 执行。"
              : "The web app is only a secure bridge. Tasks, tools, files, and Computer Use still run on the Loom Host on this computer."}
          </p>

          <div className="smirel-web-capabilities" aria-label={zh ? "Loom Web 特性" : "Loom Web features"}>
            <div>
              <b>01</b>
              <span><strong>{zh ? "本机执行" : "Runs locally"}</strong><small>{zh ? "Agent Runtime 与权限链路保持在本机" : "Agent Runtime and approvals stay on your computer"}</small></span>
            </div>
            <div>
              <b>02</b>
              <span><strong>{zh ? "同一会话" : "Same workspace"}</strong><small>{zh ? "Web 与 Desktop 共用模型、会话与工具" : "Web and Desktop share models, sessions, and tools"}</small></span>
            </div>
            <div>
              <b>03</b>
              <span><strong>{zh ? "安全中继" : "Secure relay"}</strong><small>{zh ? "浏览器不直接暴露你的 Loom Host" : "Your Loom Host is never exposed directly to the browser"}</small></span>
            </div>
          </div>
        </section>
        {children}
      </main>

      <footer className="smirel-web-footer">
        <span>SMIREL / LOOM</span>
        <span>{zh ? "个人智能体工作区" : "Personal agent workspace"}</span>
      </footer>
    </div>
  );
}

export function WebAppGate({ children }: { children: ReactNode }) {
  const account = useAccount();
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const copy = COPY[zh ? "zh" : "en"];
  const web = isLoomWebRuntime();
  const selectedDeviceId = web ? selectedWebDeviceId() : "";
  const remoteMode = web ? webExecutionMode() === "remote" : false;
  const [hostState, setHostState] = useState<HostState>("idle");
  const [hostError, setHostError] = useState("");
  const [selectedDeviceName, setSelectedDeviceName] = useState("");
  const [remoteOpen, setRemoteOpen] = useState(false);

  useEffect(() => {
    if (!web) return;
    document.documentElement.dataset.loomWeb = "true";
    document.title = "Loom Web · Smirel";
    return () => {
      delete document.documentElement.dataset.loomWeb;
    };
  }, [web]);

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

  const remoteSurface = (
    <>
      <RemoteSidebarEntry />
      <RemoteControlSurface open={remoteOpen} onClose={() => setRemoteOpen(false)} />
    </>
  );

  if (!account.ready || !account.account.authenticated || !account.account.user) {
    return (
      <SmirelShell>
        <div className="smirel-web-auth-slot" aria-label={zh ? "Loom Web 登录" : "Loom Web sign in"}>
          <div className="smirel-web-card-meta">
            <span>LOOM ACCOUNT</span>
            <i><b aria-hidden="true" />SECURE ACCESS</i>
          </div>
        </div>
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
      </SmirelShell>
    );
  }

  if (hostState !== "online") {
    const unbound = hostState === "unbound";
    const checking = hostState === "checking" || hostState === "idle";
    const title = remoteMode
      ? checking ? copy.connectRemote : copy.remoteOffline
      : unbound ? copy.connectLocal : checking ? copy.connectingLocal : copy.localOffline;
    const detail = remoteMode
      ? checking ? copy.remoteConnecting : copy.remoteOfflineHint
      : unbound ? copy.localSetup : checking ? copy.localConnecting : copy.localOfflineHint;

    return (
      <>
        <SmirelShell compact>
          <section className="smirel-web-connect-card" aria-label={remoteMode ? copy.remoteDevices : zh ? "Loom Host 连接" : "Loom Host connection"}>
            <div className="smirel-web-card-meta">
              <span>{remoteMode ? "LOOM REMOTE" : "LOOM HOST"}</span>
              <i className={checking ? "is-pending" : unbound ? "is-ready" : "is-offline"}><b aria-hidden="true" />{checking ? "CONNECTING" : unbound ? "READY" : "OFFLINE"}</i>
            </div>
            <img className="smirel-web-card-logo" src={SMIREL_LOGO} alt="Smirel" />
            <h2>{title}</h2>
            {remoteMode && selectedDeviceName ? <div className="web-host-selected-device">{selectedDeviceName}</div> : null}
            <p>{detail}</p>
            {checking ? <div className="smirel-web-progress" aria-hidden="true"><span /></div> : null}
            {!unbound && !checking && hostError ? <p className="smirel-web-error">{hostError}</p> : null}
            <div className="web-host-gate-actions">
              <button type="button" className="button secondary" onClick={() => setRemoteOpen(true)}>{copy.remoteDevices}</button>
            </div>
            <div className="smirel-web-connection-note">
              <span className="smirel-web-lock" aria-hidden="true">⌁</span>
              {zh ? "Remote 设备不会被自动选择。远程控制仍需从 Loom Remote 明确进入。" : "Remote devices are never selected automatically. Remote control remains an explicit Loom Remote action."}
            </div>
          </section>
        </SmirelShell>
        {remoteSurface}
      </>
    );
  }

  return <>{children}{remoteSurface}</>;
}
