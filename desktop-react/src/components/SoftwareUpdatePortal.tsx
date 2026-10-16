import {
  AlertCircle,
  Check,
  Download,
  ExternalLink,
  RefreshCw,
  RotateCw,
} from "./icons";
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import "./software-update.css";

import { statusCopy, type SoftwareUpdateState } from "./softwareUpdateStatus";

interface UpdateBridge {
  setUpdatePreferences(patch: { automaticUpdates?: boolean; silentUpdates?: boolean; autoRestartAfterUpdate?: boolean }): Promise<SoftwareUpdateState>;
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
  return document.querySelector<HTMLElement>('.settings-page-surface[data-page="general"] .general-update-mount');
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


function UpdatePanel() {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [state, setState] = useState<SoftwareUpdateState | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const copy = statusCopy(state);
  const releaseDate = formatReleaseDate(state?.releaseDate);
  const progress = Math.max(0, Math.min(100, Number(state?.percent ?? 0)));
  const busy = actionBusy || state?.phase === "checking" || state?.phase === "downloading";
  const canInstall = state?.enabled === true && state.accountUpdatesAllowed !== false && state.phase === "downloaded";
  const canCheck = state?.enabled === true && !busy;

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
        ...(current ?? {}),
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
        ...(current ?? {}),
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

      <div className="software-update-preferences">
        {([
          ["automaticUpdates", zh ? "自动更新" : "Automatic updates", zh ? "自动检查并下载更新；关闭后仍可手动检查。" : "Check and download automatically. Manual checks are always available."],
          ["silentUpdates", zh ? "静默更新" : "Silent updates", zh ? "后台更新时不弹出提示窗口。" : "Update in the background without popup windows."],
          ["autoRestartAfterUpdate", zh ? "自动安装并重启" : "Restart after update", zh ? "更新下载完成后，任务空闲时自动安装、重启并打开桌面版。" : "Install and restart when the download is ready and Loom is idle."],
        ] as const).map(([key, title, detail]) => (
          <label key={key} className="software-update-preference">
            <span><strong>{title}</strong><small>{detail}</small></span>
            <input type="checkbox" role="switch" checked={state?.[key] !== false} disabled={!state || actionBusy}
              onChange={async (event) => {
                const checked = event.target.checked;
                setActionBusy(true);
                try { setState(await bridge().setUpdatePreferences({ [key]: checked })); }
                catch (error) { setState(current => current ? { ...current, error: String(error), phase: "error" } : current); }
                finally { setActionBusy(false); }
              }} />
          </label>
        ))}
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
          {state?.hostRuntime?.enabled ? <p className="software-update-footnote">Host {state.hostRuntime.currentVersion} · {state.hostRuntime.phase}{state.hostRuntime.availableVersion ? ` → ${state.hostRuntime.availableVersion}` : ""}{state.hostRuntime.error ? ` · ${state.hostRuntime.error}` : ""}</p> : null}

          <div className="software-update-footnote">
            <span>{formatCheckedAt(state?.checkedAt)}</span>
            <span>{state?.automaticUpdates === false ? "自动更新已关闭，可手动检查" : "自动更新已开启"}</span>
          </div>
        </div>

        <div className="software-update-actions">
          {canInstall ? (
            <button type="button" className="software-update-primary" disabled={actionBusy} onClick={() => void installNow()}>
              <RotateCw size={14} />Restart and update
            </button>
          ) : (
            <button type="button" className="software-update-primary" disabled={!canCheck} onClick={() => void checkNow()}>
              <RefreshCw size={14} spinning={busy} />{state?.phase === "checking" ? "Checking…" : state?.phase === "downloading" ? "Downloading…" : "Check for updates"}
            </button>
          )}
          {canInstall ? (
            <button type="button" className="software-update-secondary" disabled={!canCheck} onClick={() => void checkNow()}>
              <RefreshCw size={14} />检查更新
            </button>
          ) : null}
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
