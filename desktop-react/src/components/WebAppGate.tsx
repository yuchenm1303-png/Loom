import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { RemoteControlSurface } from "./RemoteDevicesPanel";
import { RemoteSidebarEntry } from "./RemoteSidebarEntry";
import { WebPortal, type PortalHostState } from "./WebPortal";
import { useAccount } from "../state/useAccount";
import {
  isLoomWebRuntime,
  selectedWebDeviceId,
  webExecutionMode,
  type WebDeviceStatus,
} from "../webBridge";
import "./web-smirel.css";

export function WebAppGate({ children }: { children: ReactNode }) {
  const account = useAccount();
  const web = isLoomWebRuntime();
  const selectedDeviceId = web ? selectedWebDeviceId() : "";
  const remoteMode = web ? webExecutionMode() === "remote" : false;
  const [hostState, setHostState] = useState<PortalHostState>("idle");
  const [hostError, setHostError] = useState("");
  const [selectedDeviceName, setSelectedDeviceName] = useState("");
  const [remoteOpen, setRemoteOpen] = useState(false);
  const [enterWeb, setEnterWeb] = useState(() => {
    if (typeof window === "undefined") return false;
    const params = new URLSearchParams(window.location.search);
    return Boolean(params.get("local_device") || params.get("device") || window.location.hash === "#app");
  });

  useEffect(() => {
    if (!web) return;
    document.documentElement.dataset.loomWeb = "true";
    document.title = "Loom · Smirel";
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
        setHostError("Loom Host is offline.");
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
  }, [account.account.authenticated, account.account.user, account.ready, selectedDeviceId, web]);

  if (!web) return children;

  const remoteSurface = (
    <>
      <RemoteSidebarEntry />
      <RemoteControlSurface open={remoteOpen} onClose={() => setRemoteOpen(false)} />
    </>
  );

  const authenticated = Boolean(account.ready && account.account.authenticated && account.account.user);
  const showPortal = !enterWeb || !authenticated || hostState !== "online";
  if (showPortal) {
    return (
      <>
        <WebPortal
          account={account}
          hostState={hostState}
          hostError={hostError}
          selectedDeviceName={selectedDeviceName}
          remoteMode={remoteMode}
          onEnter={() => setEnterWeb(true)}
          onRemote={() => setRemoteOpen(true)}
        />
        {remoteSurface}
      </>
    );
  }

  return <>{children}{remoteSurface}</>;
}
