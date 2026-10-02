import type { ReactNode } from "react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useAccount } from "../state/useAccount";
import { isLoomWebRuntime, webExecutionMode } from "../webBridge";
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
  const remote = webExecutionMode() === "remote";
  const [hostState, setHostState] = useState<HostState>("idle");
  const [hostError, setHostError] = useState("");
  const [localHost, setLocalHost] = useState<LocalLoomHost | null>(null);
  const [discoveryNonce, setDiscoveryNonce] = useState(0);
  const [pairing, setPairing] = useState(false);
  const pairingRef = useRef(false);
  const [pairAttempts, setPairAttempts] = useState(0);
  const [pairingSucceeded, setPairingSucceeded] = useState(false);
  const [entered, setEntered] = useState(false);
  const hostStateRef = useRef<HostState>("idle");
  const connectPromiseRef = useRef<Promise<void> | null>(null);
  const activeAccountId = account.account.authenticated ? String(account.account.user?.id || "") : "";
  const accountIdRef = useRef(activeAccountId);
  accountIdRef.current = activeAccountId;

  const setTrackedHostState = useCallback((next: HostState) => {
    hostStateRef.current = next;
    setHostState(next);
  }, []);

  useEffect(() => {
    setEntered(false);
    setTrackedHostState("idle");
    setPairAttempts(0);
    setPairingSucceeded(false);
    setPairing(false);
    setLocalHost(null);
  }, [activeAccountId, setTrackedHostState]);

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

  useEffect(() => {
    if (!account.ready || !account.account.authenticated || !account.account.user) setEntered(false);
  }, [account.account.authenticated, account.account.user, account.ready]);

  const connectCurrentHost = useCallback(async (options?: { force?: boolean; background?: boolean }) => {
    if (!web || !account.ready || !account.account.authenticated) return;
    const force = Boolean(options?.force);
    if (!force && hostStateRef.current === "online") return;
    if (connectPromiseRef.current) return connectPromiseRef.current;

    if (hostStateRef.current !== "online" && !options?.background) setTrackedHostState("connecting");
    const attempt = (async () => {
      const identity = activeAccountId;
      try {
        await window.loom.connect();
        if (accountIdRef.current !== identity) return;
        setTrackedHostState("online");
        setEntered(true);
        setHostError("");
      } catch (cause) {
        if (accountIdRef.current !== identity) return;
        const error = cause as Error & { code?: string };
        setEntered(false);
        setTrackedHostState("offline");
        setHostError(error.message || String(cause));
      }
    })();

    connectPromiseRef.current = attempt;
    try {
      await attempt;
    } finally {
      if (connectPromiseRef.current === attempt) connectPromiseRef.current = null;
    }
  }, [account.account.authenticated, account.ready, activeAccountId, setTrackedHostState, web]);

  useEffect(() => {
    if (!web || !account.ready || !account.account.authenticated) return;
    void connectCurrentHost();
  }, [account.account.authenticated, account.ready, connectCurrentHost, web]);

  useEffect(() => {
    if (!web || !account.ready || !account.account.authenticated) return;
    const timer = window.setInterval(() => {
      if (hostStateRef.current === "offline" || hostStateRef.current === "missing") {
        void connectCurrentHost({ background: true });
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [account.account.authenticated, account.ready, connectCurrentHost, web]);

  useEffect(() => {
    if (!web || !account.ready || !account.account.authenticated) return;
    const onDeviceStatus = (event: Event) => {
      const detail = (event as CustomEvent<DeviceStatus>).detail;
      if (detail?.online) {
        if (hostStateRef.current !== "online") void connectCurrentHost();
        return;
      }
      if (hostStateRef.current === "online") {
        setEntered(false);
        setTrackedHostState("offline");
      }
    };
    window.addEventListener("loom:web-device-status", onDeviceStatus);
    return () => window.removeEventListener("loom:web-device-status", onDeviceStatus);
  }, [account.account.authenticated, account.ready, connectCurrentHost, setTrackedHostState, web]);

  useEffect(() => {
    if (!web || !account.ready || !account.account.authenticated || !account.account.user) return;
    if (remote) return;
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
          if (hostStateRef.current !== "online") void connectCurrentHost();
        } else if (pairing && hostStateRef.current !== "online") {
          setTrackedHostState("pairing");
        } else if (pairingSucceeded && hostStateRef.current !== "online") {
          setTrackedHostState("connecting");
        }
      } else {
        setLocalHost(null);
        const current = hostStateRef.current;
        if (current !== "online" && current !== "connecting" && current !== "pairing") {
          setTrackedHostState("missing");
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
    connectCurrentHost,
    discoveryNonce,
    pairing,
    pairingSucceeded,
    setTrackedHostState,
    web,
    remote,
  ]);

  useEffect(() => {
    if (
      !web
      || remote
      || !account.ready
      || !account.account.authenticated
      || !localHost
      || localHost.relayReady
      || pairingRef.current
      || pairingSucceeded
      || pairAttempts >= AUTO_PAIR_ATTEMPTS
    ) return;

    let cancelled = false;
    const controller = new AbortController();
    const delay = pairAttempts === 0 ? 350 : 1_500 * (pairAttempts + 1);
    const timer = window.setTimeout(() => {
      void (async () => {
        if (cancelled) return;
        pairingRef.current = true;
        setPairing(true);
        if (hostStateRef.current !== "online") setTrackedHostState("pairing");
        setHostError("");
        const result = await pairLocalLoomHost(controller.signal);
        pairingRef.current = false;
        if (cancelled) return;
        setPairing(false);
        setPairAttempts((value) => value + 1);
        if (result.ok) {
          setPairingSucceeded(true);
          if (hostStateRef.current !== "online") setTrackedHostState("connecting");
          setHostError("");
          setDiscoveryNonce((value) => value + 1);
        } else if (hostStateRef.current !== "online") {
          setTrackedHostState("offline");
          setHostError(result.error);
        }
      })();
    }, delay);

    return () => {
      cancelled = true;
      controller.abort();
      pairingRef.current = false;
      setPairing(false);
      window.clearTimeout(timer);
    };
  }, [
    account.account.authenticated,
    activeAccountId,
    account.ready,
    localHost?.deviceId,
    localHost?.relayReady,
    pairAttempts,
    pairingSucceeded,
    setTrackedHostState,
    web,
    remote,
  ]);

  const retry = useCallback(() => {
    setEntered(false);
    setPairAttempts(0);
    setPairingSucceeded(false);
    setPairing(false);
    setHostError("");
    setTrackedHostState("discovering");
    setDiscoveryNonce((value) => value + 1);
    void connectCurrentHost({ force: true });
  }, [connectCurrentHost, setTrackedHostState]);

  const enterOrRetry = useCallback(() => {
    if (hostStateRef.current === "online") {
      setEntered(true);
      return;
    }
    retry();
  }, [retry]);

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

  if (!account.ready || !account.account.authenticated || !account.account.user || hostState !== "online" || !entered) {
    return (
      <WebPortal
        account={account}
        hostState={portalState}
        hostError={hostError}
        selectedDeviceName={localHost?.deviceName || ""}
        hostDetected={Boolean(localHost)}
        onEnter={enterOrRetry}
      />
    );
  }

  return children;
}
