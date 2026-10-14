import { Download, ExternalLink } from "./icons";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import { useSettingsRoute } from "./settingsNavigation";
import "./SettingsComputerLogExport.css";

type DiagnosticLogKind = "computer" | "browser";

function humanBytes(value: unknown): string {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function labels(kind: DiagnosticLogKind, zh: boolean) {
  if (kind === "browser") {
    return {
      export: zh ? "导出浏览器日志" : "Export browser logs",
      exporting: zh ? "正在导出…" : "Exporting…",
      fallbackError: zh ? "未能生成浏览器日志归档。" : "Could not create a browser log archive.",
    };
  }
  return {
    export: zh ? "导出日志" : "Export logs",
    exporting: zh ? "正在导出…" : "Exporting…",
    fallbackError: zh ? "未能生成桌面日志归档。" : "Could not create a desktop log archive.",
  };
}

export function SettingsComputerLogExport() {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const route = useSettingsRoute();
  const kind: DiagnosticLogKind | null = route === "computer" ? "computer" : route === "browser" ? "browser" : null;
  const [busy, setBusy] = useState(false);
  const [archivePath, setArchivePath] = useState("");
  const [summary, setSummary] = useState("");
  const [error, setError] = useState("");
  const [mount, setMount] = useState<HTMLElement | null>(null);

  useEffect(() => {
    const syncMount = () => setMount(document.querySelector<HTMLElement>(".settings-log-export-mount"));
    syncMount();
    const observer = new MutationObserver(syncMount);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [kind]);

  useEffect(() => {
    setArchivePath("");
    setSummary("");
    setError("");
  }, [kind]);

  async function exportLogs() {
    if (!kind) return;
    setBusy(true);
    setError("");
    const label = labels(kind, zh);
    try {
      const result = kind === "browser" ? await window.loom.exportBrowserLogs() : await window.loom.exportComputerLogs();
      if (result.cancelled) return;
      if (!result.ok || !result.archivePath) {
        throw new Error(label.fallbackError);
      }
      setArchivePath(result.archivePath);
      const pieces = [
        result.fileCount ? `${result.fileCount} ${zh ? "个文件" : "files"}` : "",
        humanBytes(result.sizeBytes),
      ].filter(Boolean);
      setSummary(pieces.join(" · "));
      await window.loom.revealPath(result.archivePath);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  async function revealArchive() {
    if (!archivePath) return;
    try {
      await window.loom.revealPath(archivePath);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  const logExport = kind ? (() => {
    const label = labels(kind, zh);
    return (
      <div className="settings-computer-log-export" role="status" aria-live="polite">
        <button type="button" onClick={() => void exportLogs()} disabled={busy}>
          <Download size={14} strokeWidth={1.85} />
          <span>{busy ? label.exporting : label.export}</span>
        </button>
        {archivePath ? (
          <button type="button" className="settings-computer-log-reveal" onClick={() => void revealArchive()} title={archivePath}>
            <ExternalLink size={13} strokeWidth={1.8} />
            <span>{summary || (zh ? "日志已导出" : "Logs exported")}</span>
          </button>
        ) : null}
        {error ? <span className="settings-computer-log-error" title={error}>{error}</span> : null}
      </div>
    );
  })() : null;

  return mount ? createPortal(logExport, mount) : null;
}
