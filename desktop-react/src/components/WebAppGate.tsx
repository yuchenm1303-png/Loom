import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AccountDialog } from "./AccountDialog";
import { useI18n } from "../i18n";
import { useAccount } from "../state/useAccount";
import { isLoomWebRuntime, localWebDeviceId, selectWebDevice } from "../webBridge";
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

type HostState = "idle" | "discovering" | "pairing" | "connecting" | "online" | "missing" | "offline";
type RelayDevice = { id?: string; name?: string; platform?: string; version?: string };
type DeviceStatus = {
  online?: boolean;
  selectedDeviceId?: string | null;
  device?: RelayDevice | null;
  devices?: RelayDevice[];
};

const SMIREL_LOGO = "/smirel-logo.svg";
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
  const [hostState, setHostState] = useState<HostState>("idle");
  const [hostError, setHostError] = useState("");
  const [localHost, setLocalHost] = useState<LocalLoomHost | null>(null);
  const [localDeviceId, setLocalDeviceId] = useState(() => (web ? localWebDeviceId() : ""));
  const [remoteDeviceId, setRemoteDeviceId] = useState("");
  const [devices, setDevices] = useState<RelayDevice[]>([]);
  const [showRemote, setShowRemote] = useState(false);
  const [remoteLoading, setRemoteLoading] = useState(false);
  const [discoveryNonce, setDiscoveryNonce] = useState(0);
  const [pairing, setPairing] = useState(false);
  const [pairAttempts, setPairAttempts] = useState(0);
  const [pairingSucceeded, setPairingSucceeded] = useState(false);

  const activeDeviceId = remoteDeviceId || localDeviceId;
  const localHostDeviceId = localHost?.deviceId ?? "";
  const localRelayReady = localHost?.relayReady ?? null;
  const remoteDevices = useMemo(
    () => devices.filter((device) => device.id && device.id !== localDeviceId),
    [devices, localDeviceId],
  );

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
    if (!web || !account.ready || !account.account.authenticated || !account.account.user || remoteDeviceId) return;
    let cancelled = false;
    let timer: number | null = null;
    let inFlight = false;

    const probe = async () => {
      if (inFlight || cancelled) return;
      inFlight = true;
      if (!localDeviceId) setHostState("discovering");
      const host = await discoverLocalLoomHost();
      inFlight = false;
      if (cancelled) return;

      if (host) {
        rememberLocalLoomHost(host.deviceId);
        setLocalHost(host);
        setLocalDeviceId(host.deviceId);
        if (host.relayReady) {
          setPairAttempts(0);
          setPairingSucceeded(false);
          setHostError("");
          setHostState((current) => current === "online" ? current : "connecting");
        } else if (pairing) {
          setHostState("pairing");
        } else if (pairingSucceeded) {
          setHostState("connecting");
        } else {
          setHostState("offline");
        }
      } else {
        setLocalHost(null);
        if (!localDeviceId) {
          setHostState("missing");
          setHostError("");
        }
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
    discoveryNonce,
    localDeviceId,
    pairing,
    pairingSucceeded,
    remoteDeviceId,
    web,
  ]);

  useEffect(() => {
    if (
      !web
      || !account.ready
      || !account.account.authenticated
      || remoteDeviceId
      || !localHostDeviceId
      || localRelayReady !== false
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
    localHostDeviceId,
    localRelayReady,
    pairAttempts,
    pairing,
    pairingSucceeded,
    remoteDeviceId,
    web,
  ]);

  const connectActiveDevice = useCallback(async () => {
    if (!web || !activeDeviceId || !account.account.authenticated) return;
    setHostState("connecting");
    try {
      await selectWebDevice(activeDeviceId);
      await window.loom.connect();
      setHostState("online");
      setHostError("");
    } catch (cause) {
      const error = cause as Error & { code?: string };
      setHostState("offline");
      setHostError(error.message || String(cause));
    }
  }, [account.account.authenticated, activeDeviceId, web]);

  useEffect(() => {
    if (!web || !account.ready || !account.account.authenticated || !activeDeviceId) return;
    if (!remoteDeviceId && localRelayReady === false) return;
    void connectActiveDevice();
  }, [
    account.account.authenticated,
    account.ready,
    activeDeviceId,
    connectActiveDevice,
    localRelayReady,
    remoteDeviceId,
    web,
  ]);

  useEffect(() => {
    if (!web || !account.ready || !account.account.authenticated) return;
    const onDeviceStatus = (event: Event) => {
      const detail = (event as CustomEvent<DeviceStatus>).detail;
      if (Array.isArray(detail?.devices)) setDevices(detail.devices);
      if (!activeDeviceId || detail?.selectedDeviceId !== activeDeviceId) return;
      if (!detail.online) {
        setHostState("offline");
        setHostError((current) => current || (
          remoteDeviceId
            ? (zh ? "远程 Loom 设备已离线。" : "That remote Loom device is offline.")
            : (zh ? "这台电脑上的 Loom Host 正在重新连接。" : "Loom Host on this computer is reconnecting.")
        ));
        return;
      }
      void connectActiveDevice();
    };
    window.addEventListener("loom:web-device-status", onDeviceStatus);
    return () => window.removeEventListener("loom:web-device-status", onDeviceStatus);
  }, [account.account.authenticated, account.ready, activeDeviceId, connectActiveDevice, remoteDeviceId, web, zh]);

  const loadRemoteDevices = useCallback(async () => {
    setShowRemote((current) => !current);
    if (showRemote) return;
    setRemoteLoading(true);
    try {
      await selectWebDevice("");
    } catch (cause) {
      setHostError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      window.setTimeout(() => setRemoteLoading(false), 350);
    }
  }, [showRemote]);

  const connectRemote = useCallback(async (deviceId: string) => {
    const id = String(deviceId || "").trim();
    if (!id) return;
    setRemoteDeviceId(id);
    setShowRemote(false);
    setHostError("");
    setHostState("connecting");
    try {
      await selectWebDevice(id);
      await window.loom.connect();
      setHostState("online");
    } catch (cause) {
      const error = cause as Error & { code?: string };
      setHostState("offline");
      setHostError(error.message || String(cause));
    }
  }, []);

  const retryLocalPairing = useCallback(() => {
    setPairAttempts(0);
    setPairingSucceeded(false);
    setPairing(false);
    setHostError("");
    setHostState(localHostDeviceId ? "offline" : "discovering");
    setDiscoveryNonce((value) => value + 1);
  }, [localHostDeviceId]);

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
    const missing = hostState === "missing" && !localDeviceId;
    const pairingExhausted = Boolean(
      localHostDeviceId
      && localRelayReady === false
      && pairAttempts >= AUTO_PAIR_ATTEMPTS
      && !pairingSucceeded
    );
    const remote = Boolean(remoteDeviceId);
    const pending = discovering || connecting || pairingHost;

    const title = remote
      ? (connecting
          ? (zh ? "正在连接远程 Loom…" : "Connecting to remote Loom…")
          : (zh ? "远程 Loom 已离线" : "Remote Loom is offline"))
      : pairingHost
        ? (zh ? "正在安全关联 Loom Host…" : "Linking Loom Host securely…")
        : discovering
          ? (zh ? "正在查找这台电脑上的 Loom…" : "Finding Loom on this computer…")
          : connecting
            ? (zh ? "正在连接这台电脑…" : "Connecting to this computer…")
            : missing
              ? (zh ? "在这台电脑上启用 Loom" : "Enable Loom on this computer")
              : pairingExhausted
                ? (zh ? "Loom Host 需要重新关联" : "Loom Host needs attention")
                : (zh ? "正在重新连接 Loom Host…" : "Reconnecting Loom Host…");

    const detail = remote
      ? (zh
          ? "Remote 模式只在你明确选择设备后启用；下次打开网页仍默认使用当前电脑。"
          : "Remote mode is explicit and temporary. Your local computer remains the default next time you open Loom Web.")
      : missing
        ? (zh
            ? "只需安装一次轻量 Loom Host。它会随 Windows 静默启动并自动更新；安装完成后这个页面会自己发现并连接。"
            : "Install Loom Host once. It starts quietly with Windows, updates itself, and this page connects automatically when installation finishes.")
        : pairingHost
          ? (zh
              ? "正在使用短时一次性票据授权这台电脑，不需要再次打开 Desktop 登录，也不会把网页登录凭据交给 localhost。"
              : "This computer is being authorized with a short-lived one-time ticket. No Desktop login or browser credential is exposed to localhost.")
          : pairingExhausted
            ? (zh
                ? "自动关联没有完成。点击重试即可；正常情况下你不需要打开 Desktop 窗口或手动选择设备。"
                : "Automatic linking did not finish. Retry here; normally you never need to open Desktop or choose a device manually.")
            : (zh
                ? "无需命令行、Desktop 窗口或设备选择器。Loom Web 会自动绑定并重连这台电脑上的 Host。"
                : "No command line, Desktop window, or device picker is needed. Loom Web binds and reconnects the Host on this computer automatically.");

    const statusLabel = pairingHost
      ? (zh ? "安全关联中" : "PAIRING")
      : pending
        ? (zh ? "连接中" : "CONNECTING")
        : missing
          ? (zh ? "需要安装" : "SETUP")
          : pairingExhausted
            ? (zh ? "需要重试" : "RETRY")
            : (zh ? "离线" : "OFFLINE");

    return (
      <SmirelShell compact>
        <section className="smirel-web-connect-card" aria-label={zh ? "Loom Host 连接" : "Loom Host connection"}>
          <div className="smirel-web-card-meta">
            <span>{remote ? "LOOM REMOTE" : "LOOM HOST"}</span>
            <i className={pending ? "is-pending" : missing ? "is-ready" : "is-offline"}>
              <b aria-hidden="true" />{statusLabel}
            </i>
          </div>
          <img className="smirel-web-card-logo" src={SMIREL_LOGO} alt="Smirel" />
          <h2>{title}</h2>
          <p>{detail}</p>
          {pending ? <div className="smirel-web-progress" aria-hidden="true"><span /></div> : null}

          <div className="web-gate-actions">
            {missing && isWindowsBrowser() ? (
              <a className="web-gate-link primary" href={LOOM_WINDOWS_INSTALLER_URL}>
                {zh ? "安装 Loom Host" : "Install Loom Host"}
              </a>
            ) : null}
            {missing ? (
              <button className="web-gate-button secondary" type="button" onClick={() => setDiscoveryNonce((value) => value + 1)}>
                {zh ? "重新检测" : "Check again"}
              </button>
            ) : null}
            {!remote && pairingExhausted ? (
              <button className="web-gate-button primary" type="button" onClick={retryLocalPairing}>
                {zh ? "重新安全关联" : "Retry secure link"}
              </button>
            ) : null}
            {remote ? (
              <button className="web-gate-button secondary" type="button" onClick={() => {
                setRemoteDeviceId("");
                setHostState("discovering");
                setHostError("");
                setPairAttempts(0);
                setPairingSucceeded(false);
                setDiscoveryNonce((value) => value + 1);
              }}>
                {zh ? "使用这台电脑" : "Use this computer"}
              </button>
            ) : null}
            {!remote ? (
              <button className="web-gate-button secondary" type="button" onClick={() => void loadRemoteDevices()}>
                {showRemote ? (zh ? "收起 Remote" : "Hide Remote") : "Loom Remote"}
              </button>
            ) : null}
          </div>

          {missing && !isWindowsBrowser() ? (
            <p className="web-gate-meta">{zh ? "Loom Host 安装器目前支持 Windows。" : "The Loom Host installer is currently available for Windows."}</p>
          ) : null}
          {hostError ? <p className="smirel-web-error">{hostError}</p> : null}

          {showRemote ? (
            <div className="web-gate-remote">
              <div className="web-gate-remote-title">
                <span>{zh ? "远程设备" : "Remote devices"}</span>
                <small>{remoteLoading ? (zh ? "检测中…" : "Checking…") : (zh ? "明确选择" : "Choose explicitly")}</small>
              </div>
              <div className="web-gate-device-list">
                {remoteDevices.length ? remoteDevices.map((device) => (
                  <button key={device.id} className="web-gate-device" type="button" onClick={() => void connectRemote(String(device.id || ""))}>
                    <span>
                      <strong>{device.name || (zh ? "Loom 设备" : "Loom device")}</strong><br />
                      <small>{[device.platform, device.version].filter(Boolean).join(" · ")}</small>
                    </span>
                    <span className="web-gate-dot" aria-label="Online" />
                  </button>
                )) : (
                  <p className="web-gate-meta">
                    {remoteLoading
                      ? (zh ? "正在查找你在线的 Loom 设备…" : "Looking for your online Loom devices…")
                      : (zh ? "没有其他在线 Loom 设备。" : "No other Loom devices are online.")}
                  </p>
                )}
              </div>
            </div>
          ) : null}

          <div className="smirel-web-connection-note">
            <span className="smirel-web-lock" aria-hidden="true">⌁</span>
            {zh
              ? "Remote 设备永远不会被自动选择。远程控制只会在 Loom Remote 中由你明确进入。"
              : "Remote devices are never selected automatically. Remote control remains an explicit Loom Remote action."}
          </div>
        </section>
      </SmirelShell>
    );
  }

  return children;
}
