import {
  AlertCircle,
  Check,
  Download,
  ExternalLink,
  RefreshCw,
  RotateCw,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import "./software-update.css";

type SoftwareUpdatePhase =
  | "disabled"
  | "idle"
  | "checking"
  | "available"
  | "downloading"
  | "downloaded"
  | "up-to-date"
  | "error";

interface SoftwareUpdateState {
  enabled: boolean;
  phase: SoftwareUpdatePhase;
  currentVersion: string;
  availableVersion?: string;
  releaseName?: string | null;
  releaseDate?: string;
  percent?: number;
  transferred?: number;
  total?: number;
  bytesPerSecond?: number;
  checkedAt?: string;
  error?: string;
}

interface UpdateBridge {
  getUpdateStatus(): Promise<SoftwareUpdateState>;
  checkForUpdates(): Promise<SoftwareUpdateState>;
  installUpdate(): Promise<{ accepted: boolean; state: SoftwareUpdateState }>;
  onUpdateStatus(listener: (state: SoftwareUpdateState) => void): () => void;
  openExternal(url: string): Promise<unknown>;
}

const RELEASES_URL = "https://github.com/yuchenm1303-png/Loom/releases";

function bridge(): UpdateBridge {
  return window.loom as typeof window.loom & UpdateBridge;
}

function findGeneralSettingsSurface(): HTMLElement | null {
  return document.querySelector<HTMLElement>('.settings-page-surface[data-page="general"]');
}

function formatBytes(bytes: number | undefined): string {
  if (!Number.isFinite(bytes) || !bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const digits = value >= 100 || index === 0 ? 0 : value >= 10 ? 1 : 2;
  return `${value.toFixed(digits)} ${units[index]}`;
}

function formatCheckedAt(value: string | undefined): string {
  if (!value) return "Not checked in this session";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Checked recently";
  return `Last checked ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

function formatReleaseDate(value: string | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
}

function statusCopy(state: SoftwareUpdateState | null): {
  title: string;
  detail: string;
  tone: "neutral" | "active" | "success" | "warning" | "error";
} {
  if (!state) return { title: "Reading update status", detail: "Connecting to the desktop updater…", tone: "neutral" };
  switch (state.phase) {
    case "disabled":
      return {
        title: "Updates are unavailable in this build",
        detail: "Automatic updates are enabled in installed Windows builds, not development or unpackaged sessions.",
        tone: "neutral",
      };
    case "checking":
      return { title: "Checking for updates", detail: "Looking for the newest Loom Windows release…", tone: "active" };
    case "available":
      return {
        title: `Loom ${state.availableVersion || "update"} is available`,
        detail: "The update was found and the download will begin automatically.",
        tone: "active",
      };
    case "downloading":
      return {
        title: `Downloading Loom ${state.availableVersion || "update"}`,
        detail: "You can keep working while Loom downloads the installer in the background.",
        tone: "active",
      };
    case "downloaded":
      return {
        title: `Loom ${state.availableVersion || "update"} is ready`,
        detail: "Restart when convenient to finish installing the downloaded update.",
        tone: "success",
      };
    case "up-to-date":
      return { title: "Loom is up to date", detail: `You are running the newest available version, ${state.currentVersion}.`, tone: "success" };
    case "error":
      return {
        title: "Loom couldn't check for updates",
        detail: state.error || "The update service returned an unexpected error. Try again in a moment.",
        tone: "error",
      };
    default:
      return { title: "Automatic updates are on", detail: "Loom checks in the background and downloads newer releases automatically.", tone: "neutral" };
  }
}

function UpdatePanel() {
  const [state, setState] = useState<SoftwareUpdateState | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const copy = statusCopy(state);
  const releaseDate = formatReleaseDate(state?.releaseDate);
  const progress = Math.max(0, Math.min(100, Number(state?.percent ?? 0)));
  const busy = actionBusy || state?.phase === "checking" || state?.phase === "downloading";
  const canInstall = state?.enabled === true && state.phase === "downloaded";
  const canCheck = state?.enabled === true && !busy && state.phase !== "downloaded";

  useEffect(() => {
    let disposed = false;
    const updates = bridge();
    void updates.getUpdateStatus()
      .then((next) => { if (!disposed) setState(next); })
      .catch((cause) => {
        if (!disposed) {
          setState({
            enabled: false,
            phase: "error",
            currentVersion: "—",
            error: cause instanceof Error ? cause.message : String(cause),
          });
        }
      });
    const unsubscribe = updates.onUpdateStatus((next) => {
      if (!disposed) setState(next);
    });
    return () => {
      disposed = true;
      unsubscribe();
    };
  }, []);

  const metadata = useMemo(() => {
    if (!state) return [] as { label: string; value: string }[];
    const rows = [
      { label: "Installed", value: state.currentVersion || "—" },
      { label: "Channel", value: "Stable" },
    ];
    if (state.availableVersion && state.availableVersion !== state.currentVersion) {
      rows.push({ label: "Available", value: state.availableVersion });
    }
    if (releaseDate) rows.push({ label: "Released", value: releaseDate });
    return rows;
  }, [releaseDate, state]);

  const checkNow = async () => {
    if (!canCheck) return;
    setActionBusy(true);
    try {
      const next = await bridge().checkForUpdates();
      setState(next);
    } catch (cause) {
      setState((current) => ({
        enabled: current?.enabled ?? true,
        currentVersion: current?.currentVersion ?? "—",
        ...current,
        phase: "error",
        error: cause instanceof Error ? cause.message : String(cause),
      }));
    } finally {
      setActionBusy(false);
    }
  };

  const installNow = async () => {
    if (!canInstall) return;
    setActionBusy(true);
    try {
      const result = await bridge().installUpdate();
      if (!result.accepted) {
        setState(result.state);
        setActionBusy(false);
      }
    } catch (cause) {
      setState((current) => ({
        enabled: current?.enabled ?? true,
        currentVersion: current?.currentVersion ?? "—",
        ...current,
        phase: "error",
        error: cause instanceof Error ? cause.message : String(cause),
      }));
      setActionBusy(false);
    }
  };

  return (
    <section className="settings-section software-update-section" aria-labelledby="software-update-title">
      <div className="settings-section-heading software-update-heading">
        <div>
          <h2 id="software-update-title">Software update</h2>
          <p>See the installed version, check GitHub Releases, and finish downloaded updates without leaving Loom.</p>
        </div>
        <span className="software-update-version">v{state?.currentVersion || "—"}</span>
      </div>

      <div className={`software-update-card tone-${copy.tone}`}>
        <div className="software-update-status-icon" aria-hidden="true">
          {state?.phase === "error" ? <AlertCircle size={19} /> : state?.phase === "downloaded" || state?.phase === "up-to-date" ? <Check size={19} /> : state?.phase === "downloading" || state?.phase === "available" ? <Download size={19} /> : <RefreshCw size={19} />}
        </div>

        <div className="software-update-main">
          <div className="software-update-copy">
            <strong>{copy.title}</strong>
            <span>{copy.detail}</span>
          </div>

          {state?.phase === "downloading" ? (
            <div className="software-update-progress" aria-label={`Download progress ${Math.round(progress)} percent`}>
              <div className="software-update-progress-row">
                <span>{Math.round(progress)}%</span>
                <span>{formatBytes(state.transferred)} / {formatBytes(state.total)}{state.bytesPerSecond ? ` · ${formatBytes(state.bytesPerSecond)}/s` : ""}</span>
              </div>
              <div className="software-update-progress-track"><i style={{ width: `${progress}%` }} /></div>
            </div>
          ) : null}

          <div className="software-update-meta">
            {metadata.map((item) => <span key={item.label}><em>{item.label}</em><b>{item.value}</b></span>)}
          </div>

          <div className="software-update-footnote">
            <span>{formatCheckedAt(state?.checkedAt)}</span>
            <span>Checks after launch and every 4 hours · downloads happen in the background</span>
          </div>
        </div>

        <div className="software-update-actions">
          {canInstall ? (
            <button type="button" className="software-update-primary" disabled={actionBusy} onClick={() => void installNow()}>
              <RotateCw size={14} />Restart and update
            </button>
          ) : (
            <button type="button" className="software-update-primary" disabled={!canCheck} onClick={() => void checkNow()}>
              <RefreshCw size={14} className={busy ? "spin" : ""} />{state?.phase === "checking" ? "Checking…" : state?.phase === "downloading" ? "Downloading…" : "Check for updates"}
            </button>
          )}
          <button type="button" className="software-update-secondary" onClick={() => void bridge().openExternal(RELEASES_URL)}>
            <ExternalLink size={13} />Release history
          </button>
        </div>
      </div>
    </section>
  );
}

export function SoftwareUpdatePortal() {
  const [host, setHost] = useState<HTMLElement | null>(() => findGeneralSettingsSurface());

  useEffect(() => {
    let frame: number | null = null;
    const syncHost = () => {
      if (frame !== null) return;
      frame = window.requestAnimationFrame(() => {
        frame = null;
        const next = findGeneralSettingsSurface();
        setHost((current) => current === next ? current : next);
      });
    };

    syncHost();
    const observer = new MutationObserver(syncHost);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["data-page"],
    });

    return () => {
      observer.disconnect();
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, []);

  return host ? createPortal(<UpdatePanel />, host) : null;
}
