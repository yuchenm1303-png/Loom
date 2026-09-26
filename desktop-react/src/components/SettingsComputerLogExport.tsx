import { Download, ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
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

function labels(kind: DiagnosticLogKind) {
  if (kind === "browser") {
    return {
      export: "Export browser logs",
      exporting: "Exporting browser logs…",
      fallbackError: "Browser Use log export did not return an archive path.",
    };
  }
  return {
    export: "Export logs",
    exporting: "Exporting logs…",
    fallbackError: "Computer Use log export did not return an archive path.",
  };
}

export function SettingsComputerLogExport() {
  const route = useSettingsRoute();
  const kind: DiagnosticLogKind | null = route === "computer" ? "computer" : route === "browser" ? "browser" : null;
  const [busy, setBusy] = useState(false);
  const [archivePath, setArchivePath] = useState("");
  const [summary, setSummary] = useState("");
  const [error, setError] = useState("");


  useEffect(() => {
    setArchivePath("");
    setSummary("");
    setError("");
  }, [kind]);

  async function exportLogs() {
    if (!kind) return;
    setBusy(true);
    setError("");
    const label = labels(kind);
    try {
      const result = kind === "browser" ? await window.loom.exportBrowserLogs() : await window.loom.exportComputerLogs();
      if (result.cancelled) return;
      if (!result.ok || !result.archivePath) {
        throw new Error(label.fallbackError);
      }
      setArchivePath(result.archivePath);
      const pieces = [
        result.fileCount ? `${result.fileCount} files` : "",
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
    const label = labels(kind);
    return (
      <div className="settings-computer-log-export" role="status" aria-live="polite">
        <button type="button" onClick={() => void exportLogs()} disabled={busy}>
          <Download size={14} strokeWidth={1.85} />
          <span>{busy ? label.exporting : label.export}</span>
        </button>
        {archivePath ? (
          <button type="button" className="settings-computer-log-reveal" onClick={() => void revealArchive()} title={archivePath}>
            <ExternalLink size={13} strokeWidth={1.8} />
            <span>{summary || "Logs exported"}</span>
          </button>
        ) : null}
        {error ? <span className="settings-computer-log-error" title={error}>{error}</span> : null}
      </div>
    );
  })() : null;

  return (
    <>
      {logExport}
    </>
  );
}
