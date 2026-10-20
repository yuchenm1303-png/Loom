import { BookOpen, FileText, RefreshCw } from "./icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useI18n } from "../i18n";
import "./project-agent-files-card.css";
import { withProjectReadTimeout } from "../state/projectRead";

interface ProjectAgentFile {
  name: string;
  path: string;
  exists: boolean;
  readable: boolean;
  size: number;
  content: string;
  truncated: boolean;
  error?: string;
}

interface ProjectAgentFilesResult {
  projectId: string;
  projectName?: string;
  root: string;
  files: ProjectAgentFile[];
  availableCount: number;
}

type LoomBridge = {
  call<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T>;
};

interface ProjectAgentFilesCardProps {
  projectId: string;
  open: boolean;
}

function bridge(): LoomBridge | null {
  return Reflect.get(window, "loom") as LoomBridge | null;
}

function formatSize(value?: number): string {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 102.4) / 10} KB`;
  return `${Math.round(bytes / 1024 / 102.4) / 10} MB`;
}

function fileSummary(file: ProjectAgentFile, zh: boolean): string {
  if (!file.exists) return zh ? "未找到" : "Not found";
  if (!file.readable) return file.error || (zh ? "不可读取" : "Not readable");
  const lines = file.content.split(/\r?\n/).filter((line) => line.trim()).length;
  return zh ? `${lines} 行${file.truncated ? " · 已截断" : ""}` : `${lines} lines${file.truncated ? " · truncated" : ""}`;
}

export function ProjectAgentFilesCard({ projectId, open }: ProjectAgentFilesCardProps) {
  const zh = useI18n().language === "zh-CN";
  const [result, setResult] = useState<ProjectAgentFilesResult | null>(null);
  const [selectedName, setSelectedName] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (quiet = false) => {
    if (!open || !projectId) return;
    if (!quiet) setLoading(true);
    setError("");
    try {
      const client = bridge();
      if (!client) throw new Error("Loom bridge is unavailable");
      const payload = await withProjectReadTimeout(client.call<ProjectAgentFilesResult>("project/agent_files", { projectId }));
      setResult(payload);
      const readable = payload.files.find((file) => file.readable) || payload.files.find((file) => file.exists) || payload.files[0];
      setSelectedName((current) => current || readable?.name || "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [open, projectId]);

  useEffect(() => {
    if (!open || !projectId) {
      setResult(null);
      setSelectedName("");
      setLoading(false);
      setError("");
      return;
    }
    void load();
  }, [load, open, projectId]);

  const files = result?.files ?? [];
  const selected = useMemo(() => {
    return files.find((file) => file.name === selectedName) || files.find((file) => file.readable) || files[0] || null;
  }, [files, selectedName]);

  return (
    <section className="project-agent-files-card">
      <div className="project-card-heading">
        <div>
          <strong>{zh ? "说明文件" : "Instruction files"}</strong>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={14} strokeWidth={1.85} />
          {loading ? (zh ? "读取中" : "Reading") : (zh ? "刷新" : "Refresh")}
        </button>
      </div>

      <p className="project-agent-files-help">
        {zh
          ? "自动读取项目根目录下的 AGENTS.md、AGENTS.override.md 和 LOOM.md，它们和上面的项目说明一起影响项目里的任务。"
          : "AGENTS.md, AGENTS.override.md and LOOM.md in the project root are read automatically and guide the project's tasks along with the instructions above."}
      </p>

      {error ? <div className="project-agent-files-warning">{error}</div> : null}

      <div className="project-agent-file-tabs" role="tablist" aria-label={zh ? "项目说明文件" : "Project instruction files"}>
        {files.map((file) => (
          <button
            key={file.name}
            type="button"
            className={selected?.name === file.name ? "active" : ""}
            onClick={() => setSelectedName(file.name)}
            role="tab"
            aria-selected={selected?.name === file.name}
            title={file.path}
          >
            <FileText size={13} strokeWidth={1.85} />
            <span>{file.name}</span>
            <small className={file.exists ? "exists" : "missing"}>{file.exists ? formatSize(file.size) || (zh ? "存在" : "found") : (zh ? "无" : "none")}</small>
          </button>
        ))}
        {!files.length ? (
          <div className="project-agent-file-empty-tabs">
            <BookOpen size={14} strokeWidth={1.85} />
            <span>{loading ? (zh ? "正在读取说明文件…" : "Reading the files…") : error ? (zh ? "说明文件读取失败" : "Could not read the files") : (zh ? "暂无说明文件" : "No instruction files")}</span>
          </div>
        ) : null}
      </div>

      <div className="project-agent-file-viewer">
        {selected?.readable ? (
          <>
            <div className="project-agent-file-meta">
              <span>{selected.name}</span>
              <small>{fileSummary(selected, zh)}</small>
            </div>
            <pre>{selected.content || " "}</pre>
          </>
        ) : selected ? (
          <div className="project-agent-file-empty">
            <BookOpen size={18} strokeWidth={1.8} />
            <strong>{selected.name}</strong>
            <span>{fileSummary(selected, zh)}</span>
          </div>
        ) : (
          <div className="project-agent-file-empty">
            <BookOpen size={18} strokeWidth={1.8} />
            <strong>{zh ? "没有可读取的项目说明文件" : "No instruction file to read"}</strong>
            <span>{zh ? "需要时在项目根目录创建 AGENTS.md。" : "Create AGENTS.md in the project root when you need one."}</span>
          </div>
        )}
      </div>
    </section>
  );
}
