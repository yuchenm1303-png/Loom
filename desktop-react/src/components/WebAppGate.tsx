import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { useAccount } from "../state/useAccount";
import { isLoomWebRuntime } from "../webBridge";
import {
  discoverLocalLoomHost,
  pairLocalLoomHost,
  rememberLocalLoomHost,
  type LocalLoomHost,
} from "../localHostDiscovery";
import { WebPortal, type PortalHostState } from "./WebPortal";
import "./web-smirel.css";

type HostState = "idle" | "discovering" | "pairing" | "connecting" | "online" | "missing" | "offline";
type DeviceStatus = { online?: boolean; device?: { id?: string; name?: string; platform?: string; version?: string } | null };

const DISCOVERY_INTERVAL_MS = 1_500;
const AUTO_PAIR_ATTEMPTS = 3;

export function WebAppGate({ children }: { children: ReactNode }) {
  const account = useAccount();
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

  const portalState: PortalHostState = hostState === "online"
    ? "online"
    : hostState === "discovering" || hostState === "pairing" || hostState === "connecting"
      ? "checking"
      : hostState === "missing"
        ? "unbound"
        : hostState === "offline"
          ? "offline"
          : "idle";

  if (!account.ready || !account.account.authenticated || !account.account.user || hostState !== "online") {
    return (
      <WebPortal
        account={account}
        hostState={portalState}
        hostError={hostError}
        selectedDeviceName={localHost?.deviceName || ""}
        onEnter={retry}
      />
    );
  }

  return children;
}
