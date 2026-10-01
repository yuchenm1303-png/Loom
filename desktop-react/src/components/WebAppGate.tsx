import type { ReactNode } from "react";
import { ArrowDownToLine, Laptop, LockKeyhole, ShieldCheck, Layers3, ArrowUpRight } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AccountDialog } from "./AccountDialog";
import { useI18n } from "../i18n";
import { useAccount } from "../state/useAccount";
import { isLoomWebRuntime } from "../webBridge";
import {
  discoverLocalLoomHost,
  isWindowsBrowser,
  LOOM_WINDOWS_INSTALLER_URL,
  pairLocalLoomHost,
  rememberLocalLoomHost,
  type LocalLoomHost,
} from "../localHostDiscovery";
import "./web-smirel.css";
import "../web-gate.css";
import "./web-gate-surface.css";

type HostState = "idle" | "discovering" | "pairing" | "connecting" | "online" | "missing" | "offline";
type DeviceStatus = { online?: boolean; device?: { id?: string; name?: string; platform?: string; version?: string } | null };

const SMIREL_LOGO = "/smirel-logo.png";
const DISCOVERY_INTERVAL_MS = 1_500;
const AUTO_PAIR_ATTEMPTS = 3;

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
          <span className="smirel-web-product">Loom<small>PERSONAL AI AGENT</small></span>
        </a>
        <span className="smirel-web-status"><i aria-hidden="true" />{zh ? "安全连接" : "Secure connection"}</span>
      </header>

      <main className="smirel-web-layout">
        <section className="smirel-web-intro" aria-label="Smirel Loom Web">
          <span className="smirel-web-kicker"><span /> LOOM · LOCAL-FIRST AGENT</span>
          <h1>
            {zh ? "你的 Loom，" : "Your Loom stays"}<br />
            <span>{zh ? "始终在你的电脑上。" : "on your computer."}</span>
          </h1>
          <p>
            {zh
              ? "桌面端运行你的智能体。浏览器让你随时回到同一个工作区，继续会话、访问文件并管理审批。"
              : "Your desktop runs the agent. Your browser brings you back to the same conversations, files, and approvals."}
          </p>

          <a className="smirel-web-download" href={LOOM_WINDOWS_INSTALLER_URL}>
            <ArrowDownToLine size={20} aria-hidden="true" /><span><strong>{zh ? "下载 Windows 版" : "Download for Windows"}</strong><small>Windows 10 / 11 · x64</small></span><ArrowUpRight size={18} aria-hidden="true" />
          </a>

          <div className="smirel-web-capabilities" aria-label={zh ? "Loom Web 特性" : "Loom Web features"}>
            <div>
              <Laptop size={18} aria-hidden="true" />
              <span><strong>{zh ? "本机执行" : "Runs locally"}</strong><small>{zh ? "Agent Runtime 与权限链路保持在本机" : "Agent Runtime and approvals stay on your computer"}</small></span>
            </div>
            <div>
              <Layers3 size={18} aria-hidden="true" />
              <span><strong>{zh ? "同一会话" : "Same workspace"}</strong><small>{zh ? "Web 与 Desktop 共用模型、会话与工具" : "Web and Desktop share models, sessions, and tools"}</small></span>
            </div>
            <div>
              <ShieldCheck size={18} aria-hidden="true" />
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
  const [hostState, setHostState] = useState<HostState>("idle");
  const [hostError, setHostError] = useState("");
  const [localHost, setLocalHost] = useState<LocalLoomHost | null>(null);
  const [discoveryNonce, setDiscoveryNonce] = useState(0);
  const [pairing, setPairing] = useState(false);
  const [pairAttempts, setPairAttempts] = useState(0);
  const [pairingSucceeded, setPairingSucceeded] = useState(false);

  useEffect(() => {
    if (!web) return;
    document.documentElement.dataset.loomWeb = "true";
    document.title = "Loom Web · Smirel";
    return () => { delete document.documentElement.dataset.loomWeb; };
  }, [web]);

  useEffect(() => {
    if (!web) return;
    const refresh = () => void account.refresh();
    window.addEventListener("loom:web-auth-changed", refresh);
    return () => window.removeEventListener("loom:web-auth-changed", refresh);
  }, [account.refresh, web]);

  const connectCurrentHost = useCallback(async () => {
    if (!web || !account.ready || !account.account.authenticated) return;
    setHostState("connecting");
    try {
      await window.loom.connect();
      setHostState("online");
      setHostError("");
    } catch (cause) {
      const error = cause as Error & { code?: string };
      setHostState("offline");
      setHostError(error.message || String(cause));
    }
  }, [account.account.authenticated, account.ready, web]);

  useEffect(() => {
    if (!web || !account.ready || !account.account.authenticated) return;
    void connectCurrentHost();
  }, [account.account.authenticated, account.ready, connectCurrentHost, web]);

  useEffect(() => {
    if (!web || !account.ready || !account.account.authenticated) return;
    const onDeviceStatus = (event: Event) => {
      const detail = (event as CustomEvent<DeviceStatus>).detail;
      if (detail?.online) {
        void connectCurrentHost();
        return;
      }
      setHostState((current) => current === "online" ? "offline" : current);
    };
    window.addEventListener("loom:web-device-status", onDeviceStatus);
    return () => window.removeEventListener("loom:web-device-status", onDeviceStatus);
  }, [account.account.authenticated, account.ready, connectCurrentHost, web]);

  useEffect(() => {
    if (!web || !account.ready || !account.account.authenticated || !account.account.user) return;
    let cancelled = false;
    let timer: number | null = null;
    let inFlight = false;

    const probe = async () => {
      if (inFlight || cancelled) return;
      inFlight = true;
      const host = await discoverLocalLoomHost();
      inFlight = false;
      if (cancelled) return;

      if (host) {
        rememberLocalLoomHost(host.deviceId);
        setLocalHost(host);
        if (host.relayReady) {
          setPairAttempts(0);
          setPairingSucceeded(false);
          void connectCurrentHost();
        } else if (pairing) {
          setHostState("pairing");
        } else if (pairingSucceeded) {
          setHostState("connecting");
        }
      } else {
        setLocalHost(null);
        setHostState((current) => (
          current === "online" || current === "connecting" || current === "pairing"
            ? current
            : "missing"
        ));
      }

      timer = window.setTimeout(probe, DISCOVERY_INTERVAL_MS);
    };

    void probe();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [
    account.account.authenticated,
    account.account.user,
    account.ready,
    connectCurrentHost,
    discoveryNonce,
    pairing,
    pairingSucceeded,
    web,
  ]);

  useEffect(() => {
    if (
      !web
      || !account.ready
      || !account.account.authenticated
      || !localHost
      || localHost.relayReady
      || pairing
      || pairingSucceeded
      || pairAttempts >= AUTO_PAIR_ATTEMPTS
    ) return;

    let cancelled = false;
    const delay = pairAttempts === 0 ? 350 : 1_500 * (pairAttempts + 1);
    const timer = window.setTimeout(() => {
      void (async () => {
        if (cancelled) return;
        setPairing(true);
        setHostState("pairing");
        setHostError("");
        const result = await pairLocalLoomHost();
        if (cancelled) return;
        setPairing(false);
        setPairAttempts((value) => value + 1);
        if (result.ok) {
          setPairingSucceeded(true);
          setHostState("connecting");
          setHostError("");
          setDiscoveryNonce((value) => value + 1);
        } else {
          setHostState("offline");
          setHostError(result.error);
        }
      })();
    }, delay);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [
    account.account.authenticated,
    account.ready,
    localHost,
    pairAttempts,
    pairing,
    pairingSucceeded,
    web,
  ]);

  const retry = useCallback(() => {
    setPairAttempts(0);
    setPairingSucceeded(false);
    setPairing(false);
    setHostError("");
    setHostState("discovering");
    setDiscoveryNonce((value) => value + 1);
    void connectCurrentHost();
  }, [connectCurrentHost]);

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
    const discovering = hostState === "idle" || hostState === "discovering";
    const connecting = hostState === "connecting";
    const pairingHost = hostState === "pairing" || pairing;
    const missing = hostState === "missing" && !localHost;
    const pairingExhausted = Boolean(
      localHost && !localHost.relayReady && pairAttempts >= AUTO_PAIR_ATTEMPTS && !pairingSucceeded
    );
    const pending = discovering || connecting || pairingHost;

    const title = pairingHost
      ? (zh ? "正在安全关联 Loom Host…" : "Linking Loom Host securely…")
      : connecting
        ? (zh ? "正在连接你的 Loom Host…" : "Connecting to your Loom Host…")
        : missing
          ? (zh ? "当前没有在线 Loom Host" : "No Loom Host is online")
          : pairingExhausted
            ? (zh ? "Loom Host 需要重新关联" : "Loom Host needs attention")
            : (zh ? "正在重新连接 Loom Host…" : "Reconnecting Loom Host…");

    const detail = missing
      ? (zh
          ? "登录后网页会自动连接这个账号当前在线的 Loom Host。若你想让这台电脑成为 Host，只需安装一次 Loom。"
          : "After sign-in, Loom Web automatically follows the current online Host for this account. Install Loom once only if you want this computer to become that Host.")
      : pairingHost
        ? (zh
            ? "正在用短时一次性票据授权这台电脑；浏览器登录凭据不会交给 localhost。"
            : "This computer is being authorized with a short-lived one-time ticket; browser credentials are never exposed to localhost.")
        : pairingExhausted
          ? (zh ? "自动关联没有完成，点击重试即可。" : "Automatic linking did not finish. Retry here.")
          : (zh
              ? "网页只做安全中继；Agent、会话、工具和权限仍全部运行在当前 Loom Host。"
              : "The web app is only a secure relay; Agent, sessions, tools, and approvals stay on the current Loom Host.");

    const statusLabel = pairingHost
      ? (zh ? "安全关联中" : "PAIRING")
      : pending
        ? (zh ? "连接中" : "CONNECTING")
        : missing
          ? (zh ? "HOST 离线" : "HOST OFFLINE")
          : pairingExhausted
            ? (zh ? "需要重试" : "RETRY")
            : (zh ? "离线" : "OFFLINE");

    return (
      <SmirelShell compact>
        <section className="smirel-web-connect-card" aria-label={zh ? "Loom Host 连接" : "Loom Host connection"}>
          <div className="smirel-web-card-meta">
            <span>LOOM HOST</span>
            <i className={pending ? "is-pending" : missing ? "is-ready" : "is-offline"}>
              <b aria-hidden="true" />{statusLabel}
            </i>
          </div>
          <div className={`smirel-web-host-symbol${pending ? " is-pending" : ""}`} aria-hidden="true"><Laptop size={30} /><span><LockKeyhole size={12} /></span></div>
          <div role="status" aria-live="polite"><h2>{title}</h2><p className="smirel-web-host-detail">{detail}</p></div>
          {pending ? <div className="smirel-web-progress" aria-hidden="true"><span /></div> : null}

          <dl className="smirel-web-host-facts">
            <div><dt>{zh ? "账户" : "Account"}</dt><dd>{account.account.user.email}</dd></div>
            <div><dt>{zh ? "当前电脑" : "This computer"}</dt><dd>{localHost?.deviceName || (zh ? "等待发现" : "Waiting for host")}</dd></div>
          </dl>

          <div className="web-gate-actions">
            {missing && isWindowsBrowser() ? (
              <a className="web-gate-link primary" href={LOOM_WINDOWS_INSTALLER_URL}>
                {zh ? "安装 Loom Host" : "Install Loom Host"}
              </a>
            ) : null}
            <button className="web-gate-button secondary" type="button" onClick={retry}>
              {zh ? "重新连接" : "Reconnect"}
            </button>
          </div>

          {missing && !isWindowsBrowser() ? (
            <p className="web-gate-meta">{zh ? "Loom Host 安装器目前支持 Windows。" : "The Loom Host installer is currently available for Windows."}</p>
          ) : null}
          {hostError ? <p className="smirel-web-error">{hostError}</p> : null}

          <button className="smirel-web-signout" type="button" disabled={account.busy} onClick={() => void account.logout()}>{zh ? "退出登录" : "Sign out"}</button>

          <div className="smirel-web-connection-note">
            <LockKeyhole size={15} aria-hidden="true" />
            {zh
              ? "一个 Loom 账号只维护一个当前 Host；新的 Desktop Host 上线时会安全替换旧 Host，所有网页登录会自动跟随。"
              : "Each Loom account has one current Host. A newly connected Desktop Host safely replaces the old one and every web session follows automatically."}
          </div>
        </section>
      </SmirelShell>
    );
  }

  return children;
}
