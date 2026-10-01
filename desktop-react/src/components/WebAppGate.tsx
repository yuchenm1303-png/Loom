import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { AccountDialog } from "./AccountDialog";
import { useI18n } from "../i18n";
import { useAccount } from "../state/useAccount";
import { isLoomWebRuntime, localWebDeviceId } from "../webBridge";
import "./web-smirel.css";

type HostState = "idle" | "checking" | "online" | "offline" | "unbound";

type DeviceStatus = {
  online?: boolean;
  selectedDeviceId?: string | null;
  device?: { id?: string; name?: string } | null;
};

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
  const web = isLoomWebRuntime();
  const localDeviceId = web ? localWebDeviceId() : "";
  const [hostState, setHostState] = useState<HostState>("idle");
  const [hostError, setHostError] = useState("");

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
    if (!web || !account.ready || !account.account.authenticated || !account.account.user) {
      setHostState("idle");
      setHostError("");
      return;
    }
    if (!localDeviceId) {
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
      const detail = (event as CustomEvent<DeviceStatus>).detail;
      if (detail?.selectedDeviceId && detail.selectedDeviceId !== localDeviceId) return;
      if (!detail?.online) {
        setHostState("offline");
        setHostError("Loom on this computer is offline.");
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
  }, [account.account.authenticated, account.account.user, account.ready, localDeviceId, web]);

  if (!web) return children;

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
    const title = unbound
      ? (zh ? "连接这台电脑上的 Loom" : "Connect Loom on this computer")
      : checking
        ? (zh ? "正在连接这台电脑…" : "Connecting to this computer…")
        : (zh ? "这台电脑上的 Loom 已离线" : "Loom on this computer is offline");
    const detail = unbound
      ? (zh
          ? "打开这台电脑上的 Loom，然后从托盘选择一次“打开 Loom Web”。浏览器会记住这台电脑，之后自动连接。"
          : "Open Loom on this computer, then choose “Open Loom Web” from the tray once. This browser will remember this computer and reconnect automatically.")
      : checking
        ? (zh ? "正在建立浏览器与本机 Loom Host 的安全连接。" : "Establishing a secure connection between this browser and your local Loom Host.")
        : (zh
            ? "启动这台电脑上的 Loom。Desktop 窗口可以关闭，Loom Host 会继续在后台保持连接。"
            : "Start Loom on this computer. The Desktop window may close while Loom Host keeps running in the background.");

    return (
      <SmirelShell compact>
        <section className="smirel-web-connect-card" aria-label={zh ? "Loom Host 连接" : "Loom Host connection"}>
          <div className="smirel-web-card-meta">
            <span>LOOM HOST</span>
            <i className={checking ? "is-pending" : unbound ? "is-ready" : "is-offline"}><b aria-hidden="true" />{checking ? "CONNECTING" : unbound ? "READY" : "OFFLINE"}</i>
          </div>
          <img className="smirel-web-card-logo" src={SMIREL_LOGO} alt="Smirel" />
          <h2>{title}</h2>
          <p>{detail}</p>
          {checking ? <div className="smirel-web-progress" aria-hidden="true"><span /></div> : null}
          {!unbound && !checking && hostError ? <p className="smirel-web-error">{hostError}</p> : null}
          <div className="smirel-web-connection-note">
            <span className="smirel-web-lock" aria-hidden="true">⌁</span>
            {zh ? "Remote 设备不会被自动选择。远程控制仍需从 Loom Remote 明确进入。" : "Remote devices are never selected automatically. Remote control remains an explicit Loom Remote action."}
          </div>
        </section>
      </SmirelShell>
    );
  }

  return children;
}
