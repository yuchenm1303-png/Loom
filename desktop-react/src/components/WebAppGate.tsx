import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AccountDialog } from "./AccountDialog";
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
import "../web-gate.css";

type HostState = "idle" | "discovering" | "pairing" | "connecting" | "online" | "missing" | "offline";

type RelayDevice = { id?: string; name?: string; platform?: string; version?: string };
type DeviceStatus = {
  online?: boolean;
  selectedDeviceId?: string | null;
  device?: RelayDevice | null;
  devices?: RelayDevice[];
};

const DISCOVERY_INTERVAL_MS = 1_500;
const AUTO_PAIR_ATTEMPTS = 3;

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
  const [pairing, setPairing] = useState(false);
  const [pairAttempts, setPairAttempts] = useState(0);
  const [pairingSucceeded, setPairingSucceeded] = useState(false);

  const activeDeviceId = remoteDeviceId || localDeviceId;
  const localRelayReady = localHost?.relayReady ?? null;
  const remoteDevices = useMemo(() => devices.filter((device) => device.id && device.id !== localDeviceId), [devices, localDeviceId]);

  useEffect(() => {
    if (!web) return;
    const refresh = () => void account.refresh();
    window.addEventListener("loom:web-auth-changed", refresh);
    return () => window.removeEventListener("loom:web-auth-changed", refresh);
  }, [account.refresh, web]);

  // Continuously discover the Host on this physical computer. Discovery is
  // read-only; all privileged Loom operations still travel through the normal
  // authenticated WSS relay after the exact local device id is selected.
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
          if (hostState !== "online") setHostState("connecting");
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
    hostState,
    localDeviceId,
    pairing,
    pairingSucceeded,
    remoteDeviceId,
    web,
  ]);

  // A newly installed Host has no account credential yet. The signed-in website
  // automatically mints a short-lived one-time pairing ticket and hands only
  // that ticket to localhost. The Host exchanges it for its own session, so the
  // browser's HttpOnly refresh token never crosses the loopback boundary.
  useEffect(() => {
    if (
      !web
      || !account.ready
      || !account.account.authenticated
      || remoteDeviceId
      || !localHost
      || localHost.relayReady
      || pairing
      || pairingSucceeded
      || pairAttempts >= AUTO_PAIR_ATTEMPTS
    ) return;

    let cancelled = false;
    const delay = pairAttempts === 0 ? 250 : 1_500 * (pairAttempts + 1);
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
        setHostError((current) => current || (remoteDeviceId ? "That remote Loom device is offline." : "Loom Host on this computer is reconnecting."));
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
      // Empty explicit selection opens only the authenticated browser socket to
      // obtain the account's device list. It never auto-selects another machine.
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
    setHostState(localHost ? "offline" : "discovering");
    setDiscoveryNonce((value) => value + 1);
  }, [localHost]);

  if (!web) return children;

  if (!account.ready || !account.account.authenticated || !account.account.user) {
    return (
      <>
        <main className="boot-error" aria-label="Loom Web sign in">
          <section className="boot-error-card web-gate-card">
            <div className="brand-mark large" aria-hidden="true">L</div>
            <h1>Loom</h1>
            <p>Sign in once. After that, opening this website is enough — Loom automatically finds, links and reconnects the Host on this computer.</p>
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
    const pairingHost = hostState === "pairing" || pairing;
    const missing = hostState === "missing" && !localDeviceId;
    const hostInstalled = Boolean(localHost || localDeviceId);
    const pairingExhausted = Boolean(localHost && !localHost.relayReady && pairAttempts >= AUTO_PAIR_ATTEMPTS && !pairingSucceeded);
    const remote = Boolean(remoteDeviceId);

    const title = remote
      ? (connecting ? "Connecting to remote Loom…" : "Remote Loom is offline")
      : pairingHost
        ? "Linking Loom Host securely…"
        : discovering
          ? "Finding Loom on this computer…"
          : connecting
            ? "Connecting to this computer…"
            : missing
              ? "Enable Loom on this computer"
              : pairingExhausted
                ? "Loom Host needs attention"
                : "Reconnecting Loom Host…";

    const description = remote
      ? "Remote mode is explicit and temporary. Your local computer remains the default the next time you open Loom Web."
      : missing
        ? "Install Loom Host once. It runs quietly in the background, starts with Windows, updates itself, and this page connects automatically as soon as installation finishes."
        : pairingHost
          ? "The website is authorizing this computer with a short-lived one-time ticket. No Desktop login or copied token is needed."
          : pairingExhausted
            ? "Automatic linking did not finish. Retry here; you normally never need to open the Desktop window or choose a device manually."
            : discovering || connecting
              ? "No command line, Desktop window or device picker is needed. Loom Web is binding itself to the Host on this physical computer."
              : hostInstalled
                ? "The local Host is installed and the page keeps reconnecting automatically."
                : "Loom Web is checking this computer.";

    return (
      <main className="boot-error" aria-label="Loom Host connection">
        <section className="boot-error-card web-gate-card">
          <div className="brand-mark large" aria-hidden="true">L</div>
          <h1>{title}</h1>
          <p>{description}</p>

          {(discovering || connecting || pairingHost) ? (
            <div className="web-gate-status-row">
              <span className="web-gate-spinner" aria-hidden="true" />
              {pairingHost ? "Securing this Host" : connecting ? "Connecting Host" : "Checking this computer"}
            </div>
          ) : null}

          <div className="web-gate-actions">
            {missing && isWindowsBrowser() ? (
              <a className="web-gate-link primary" href={LOOM_WINDOWS_INSTALLER_URL}>Install Loom Host</a>
            ) : null}
            {missing ? (
              <button className="web-gate-button secondary" type="button" onClick={() => setDiscoveryNonce((value) => value + 1)}>Check again</button>
            ) : null}
            {!remote && pairingExhausted ? (
              <button className="web-gate-button primary" type="button" onClick={retryLocalPairing}>Retry secure link</button>
            ) : null}
            {remote ? (
              <button className="web-gate-button secondary" type="button" onClick={() => {
                setRemoteDeviceId("");
                setHostState("discovering");
                setHostError("");
                setPairAttempts(0);
                setPairingSucceeded(false);
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
