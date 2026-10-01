import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AccountDialog } from "./AccountDialog";
import { useAccount } from "../state/useAccount";
import { isLoomWebRuntime, localWebDeviceId, selectWebDevice } from "../webBridge";
import {
  discoverLocalLoomHost,
  isWindowsBrowser,
  LOOM_WINDOWS_INSTALLER_URL,
  openLocalLoomHost,
  rememberLocalLoomHost,
  type LocalLoomHost,
} from "../localHostDiscovery";
import "../web-gate.css";

type HostState = "idle" | "discovering" | "connecting" | "online" | "missing" | "offline";

type RelayDevice = { id?: string; name?: string; platform?: string; version?: string };
type DeviceStatus = {
  online?: boolean;
  selectedDeviceId?: string | null;
  device?: RelayDevice | null;
  devices?: RelayDevice[];
};

const DISCOVERY_INTERVAL_MS = 1_500;

export function WebAppGate({ children }: { children: ReactNode }) {
  const account = useAccount();
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

  const activeDeviceId = remoteDeviceId || localDeviceId;
  const localRelayReady = localHost?.relayReady ?? null;
  const remoteDevices = useMemo(() => devices.filter((device) => device.id && device.id !== localDeviceId), [devices, localDeviceId]);

  useEffect(() => {
    if (!web) return;
    const refresh = () => void account.refresh();
    window.addEventListener("loom:web-auth-changed", refresh);
    return () => window.removeEventListener("loom:web-auth-changed", refresh);
  }, [account.refresh, web]);

  // The website continuously looks for a Host on *this* physical computer.
  // Discovery is read-only and loopback-only; no Agent operation is exposed on
  // localhost. Once found, the normal authenticated WSS relay is bound to that
  // exact device id.
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
        if (!host.relayReady) {
          setHostState("offline");
          setHostError("Loom Host is installed, but it is not connected to this Loom account yet.");
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
    remoteDeviceId,
    web,
  ]);

  const connectActiveDevice = useCallback(async () => {
    if (!web || !activeDeviceId || !account.account.authenticated) return;
    setHostState("connecting");
    try {
      // select_device is explicit even for local mode. The socket URL also
      // carries the remembered local id as a backwards-compatible fast path.
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
        setHostError((current) => current || (remoteDeviceId ? "That remote Loom device is offline." : "Loom Host on this computer is not connected yet."));
        return;
      }
      void connectActiveDevice();
    };
    window.addEventListener("loom:web-device-status", onDeviceStatus);
    return () => window.removeEventListener("loom:web-device-status", onDeviceStatus);
  }, [account.account.authenticated, account.ready, activeDeviceId, connectActiveDevice, remoteDeviceId, web]);

  const loadRemoteDevices = useCallback(async () => {
    setShowRemote((current) => !current);
    if (showRemote) return;
    setRemoteLoading(true);
    try {
      // An empty explicit selection opens the authenticated browser socket only
      // to obtain the user's device list; it never falls back to a remote Host.
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

  const openHost = useCallback(async () => {
    const opened = await openLocalLoomHost();
    if (!opened) setHostError("Could not open Loom Host. Start Loom from the Start menu, then this page will reconnect automatically.");
  }, []);

  if (!web) return children;

  if (!account.ready || !account.account.authenticated || !account.account.user) {
    return (
      <>
        <main className="boot-error" aria-label="Loom Web sign in">
          <section className="boot-error-card web-gate-card">
            <div className="brand-mark large" aria-hidden="true">L</div>
            <h1>Loom</h1>
            <p>Sign in once. After that, opening this website is enough — Loom automatically uses the Host on this computer and never silently jumps to another device.</p>
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
    const discovering = hostState === "idle" || hostState === "discovering";
    const connecting = hostState === "connecting";
    const missing = hostState === "missing" && !localDeviceId;
    const hostInstalled = Boolean(localHost || localDeviceId);
    const remote = Boolean(remoteDeviceId);
    const title = remote
      ? (connecting ? "Connecting to remote Loom…" : "Remote Loom is offline")
      : discovering
        ? "Finding Loom on this computer…"
        : connecting
          ? "Connecting to this computer…"
          : missing
            ? "Enable Loom on this computer"
            : "Loom Host is ready on this computer";
    const description = remote
      ? "Remote mode is explicit and temporary. Your local computer remains the default the next time you open Loom Web."
      : missing
        ? "Install the lightweight Loom Host once. It runs quietly in the background, starts with Windows, updates itself, and this page will connect automatically when installation finishes."
        : hostInstalled && localRelayReady === false
          ? "The local Host is installed. Open Loom once to sign in with the same account; after that you can close the Desktop window and use only this website."
          : discovering || connecting
            ? "No command line or device picker is needed. Loom Web is binding itself to the Host on this physical computer."
            : "The local Host was found, but its authenticated relay is not online yet. This page keeps retrying automatically.";

    return (
      <main className="boot-error" aria-label="Loom Host connection">
        <section className="boot-error-card web-gate-card">
          <div className="brand-mark large" aria-hidden="true">L</div>
          <h1>{title}</h1>
          <p>{description}</p>

          {(discovering || connecting) ? (
            <div className="web-gate-status-row"><span className="web-gate-spinner" aria-hidden="true" /> Checking this computer</div>
          ) : null}

          <div className="web-gate-actions">
            {missing && isWindowsBrowser() ? (
              <a className="web-gate-link primary" href={LOOM_WINDOWS_INSTALLER_URL}>Install Loom Host</a>
            ) : null}
            {missing ? (
              <button className="web-gate-button secondary" type="button" onClick={() => setDiscoveryNonce((value) => value + 1)}>Check again</button>
            ) : null}
            {!remote && hostInstalled && hostState === "offline" ? (
              <button className="web-gate-button primary" type="button" onClick={() => void openHost()}>Open Loom once</button>
            ) : null}
            {remote ? (
              <button className="web-gate-button secondary" type="button" onClick={() => {
                setRemoteDeviceId("");
                setHostState("discovering");
                setHostError("");
                setDiscoveryNonce((value) => value + 1);
              }}>Use this computer</button>
            ) : null}
            {!remote ? (
              <button className="web-gate-button secondary" type="button" onClick={() => void loadRemoteDevices()}>
                {showRemote ? "Hide Remote" : "Loom Remote"}
              </button>
            ) : null}
          </div>

          {missing && !isWindowsBrowser() ? <p className="web-gate-meta">The Loom Host installer is currently available for Windows.</p> : null}
          {hostError ? <p className="boot-error-detail">{hostError}</p> : null}

          {showRemote ? (
            <div className="web-gate-remote">
              <div className="web-gate-remote-title">
                <span>Remote devices</span>
                <small>{remoteLoading ? "Checking…" : "Choose explicitly"}</small>
              </div>
              <div className="web-gate-device-list">
                {remoteDevices.length ? remoteDevices.map((device) => (
                  <button key={device.id} className="web-gate-device" type="button" onClick={() => void connectRemote(String(device.id || ""))}>
                    <span>
                      <strong>{device.name || "Loom device"}</strong><br />
                      <small>{[device.platform, device.version].filter(Boolean).join(" · ")}</small>
                    </span>
                    <span className="web-gate-dot" aria-label="Online" />
                  </button>
                )) : (
                  <p className="web-gate-meta">{remoteLoading ? "Looking for your online Loom devices…" : "No other Loom devices are online."}</p>
                )}
              </div>
            </div>
          ) : null}
        </section>
      </main>
    );
  }

  return children;
}
