import { Download, ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import "./SettingsComputerLogExport.css";

function humanBytes(value: unknown): string {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function isComputerSettingsPage(): boolean {
  const heading = document.querySelector(".settings-content h1");
  return heading?.textContent?.trim() === "Computer Use";
}

export function SettingsComputerLogExport() {
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [archivePath, setArchivePath] = useState("");
  const [summary, setSummary] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const refresh = () => setVisible(isComputerSettingsPage());
    refresh();
    const observer = new MutationObserver(refresh);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    const timer = window.setInterval(refresh, 350);
    return () => {
      observer.disconnect();
      window.clearInterval(timer);
    };
  }, []);

  async function exportLogs() {
    setBusy(true);
    setError("");
    try {
      const result = await window.loom.exportComputerLogs();
      if (result.cancelled) return;
      if (!result.ok || !result.archivePath) {
        throw new Error("Computer Use log export did not return an archive path.");
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

  if (!visible) return null;

  return (
    <div className="settings-computer-log-export" role="status" aria-live="polite">
      <button type="button" onClick={() => void exportLogs()} disabled={busy}>
        <Download size={14} strokeWidth={1.85} />
        <span>{busy ? "Exporting logs…" : "Export logs"}</span>
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
}
