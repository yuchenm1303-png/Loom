import { ChevronRight, ExternalLink, FileCode2, FileDiff } from "lucide-react";
import { useMemo } from "react";
import { canRenderArtifact } from "../artifactRenderers";
import type { TranscriptItem } from "../types/loom";
import "./turn-artifacts-preview.css";

type DiffFile = {
  path: string;
  displayPath: string;
  name: string;
  additions: number;
  deletions: number;
  editCount: number;
};

function normalizePath(value: string): string {
  return String(value || "")
    .trim()
    .replaceAll("\\", "/")
    .replace(/^[ab]\//, "")
    .replace(/^\.\//, "");
}

function compactPath(path: string): string {
  const normalized = normalizePath(path);
  const lower = normalized.toLowerCase();
  const loomMarker = "/loom/";
  const loomIndex = lower.lastIndexOf(loomMarker);
  if (loomIndex >= 0) return normalized.slice(loomIndex + loomMarker.length);
  const hiddenMarker = "/.loom/";
  const hiddenIndex = lower.lastIndexOf(hiddenMarker);
  if (hiddenIndex >= 0) return `.loom/${normalized.slice(hiddenIndex + hiddenMarker.length)}`;
  return normalized;
}

function basename(path: string): string {
  const normalized = normalizePath(path);
  return normalized.split("/").filter(Boolean).at(-1) || normalized || "Workspace change";
}

function splitPatch(diff: string, fallbackPaths: string[]): Array<{ path: string; diff: string }> {
  const text = String(diff || "").replace(/\r\n/g, "\n");
  const fallback = fallbackPaths.map(normalizePath).filter(Boolean);
  if (!text.trim()) return fallback.map((path) => ({ path, diff: "" }));

  const lines = text.split("\n");
  const result: Array<{ path: string; diff: string }> = [];
  let currentPath = "";
  let current: string[] = [];

  const flush = () => {
    if (!current.length) return;
    result.push({
      path: currentPath || fallback[result.length] || fallback[0] || "Workspace change",
      diff: current.join("\n"),
    });
    current = [];
    currentPath = "";
  };

  for (const line of lines) {
    const gitHeader = /^diff --git a\/(.+?) b\/(.+)$/.exec(line);
    if (gitHeader) {
      flush();
      currentPath = normalizePath(gitHeader[2]);
      current.push(line);
      continue;
    }

    const newFileHeader = /^\+\+\+\s+(?:b\/)?(.+)$/.exec(line);
    if (!currentPath && newFileHeader && newFileHeader[1] !== "/dev/null") {
      currentPath = normalizePath(newFileHeader[1]);
    }
    current.push(line);
  }

  flush();
  return result.length ? result : [{ path: fallback[0] || "Workspace change", diff: text }];
}

function countPatch(diff: string): { additions: number; deletions: number } {
  let additions = 0;
  let deletions = 0;
  for (const line of String(diff || "").replace(/\r\n/g, "\n").split("\n")) {
    if (line.startsWith("+++") || line.startsWith("---")) continue;
    if (line.startsWith("+")) additions += 1;
    else if (line.startsWith("-")) deletions += 1;
  }
  return { additions, deletions };
}

function collectFiles(items: TranscriptItem[]): DiffFile[] {
  const collected = new Map<string, { additions: number; deletions: number; editCount: number }>();

  for (const item of items) {
    if (item.type !== "file_edit") continue;
    const paths = (item.paths ?? []).map(normalizePath).filter(Boolean);
    const patches = splitPatch(String(item.diff ?? ""), paths);

    if (!patches.length && paths.length) {
      for (const path of paths) {
        const current = collected.get(path) ?? { additions: 0, deletions: 0, editCount: 0 };
        current.editCount += 1;
        collected.set(path, current);
      }
      continue;
    }

    for (const patch of patches) {
      const path = normalizePath(patch.path) || paths[0] || "Workspace change";
      const current = collected.get(path) ?? { additions: 0, deletions: 0, editCount: 0 };
      const stats = countPatch(patch.diff);
      current.additions += stats.additions;
      current.deletions += stats.deletions;
      current.editCount += 1;
      collected.set(path, current);
    }

    for (const path of paths) {
      if (!collected.has(path)) {
        collected.set(path, { additions: 0, deletions: 0, editCount: 1 });
      }
    }
  }

  return [...collected.entries()]
    .map(([path, value]) => ({
      path,
      displayPath: compactPath(path),
      name: basename(path),
      additions: value.additions,
      deletions: value.deletions,
      editCount: value.editCount,
    }))
    .sort((a, b) => a.displayPath.localeCompare(b.displayPath));
}

function openReview(path?: string): void {
  window.dispatchEvent(new CustomEvent("loom:review-open", {
    detail: path ? { path } : {},
  }));
}

export function TurnArtifactsPreview({ items, workspace }: { items: TranscriptItem[]; workspace?: string }) {
  const files = useMemo(() => collectFiles(items), [items]);
  const totals = useMemo(() => files.reduce(
    (total, file) => ({
      additions: total.additions + file.additions,
      deletions: total.deletions + file.deletions,
    }),
    { additions: 0, deletions: 0 },
  ), [files]);

  if (!files.length) return null;

  const primary = files[0];
  const previewFile = files.find((file) => canRenderArtifact(file.path)) ?? null;
  const title = files.length === 1 ? `已编辑 ${primary.name}` : `已修改 ${files.length} 个文件`;

  return (
    <section className="turn-artifacts rich-turn-artifacts review-summary-card" aria-label="Changed files">
      <button
        type="button"
        className="turn-artifacts-header review-summary-header"
        onClick={(event) => {
          event.stopPropagation();
          openReview(primary.path);
        }}
        title="在右侧审查面板查看代码修改"
      >
        <span className="turn-artifacts-icon" aria-hidden="true"><FileDiff size={15} /></span>
        <span className="turn-artifacts-copy">
          <strong>{title}</strong>
          {(totals.additions || totals.deletions) ? (
            <span className="turn-artifacts-stats"><b>+{totals.additions}</b><i>-{totals.deletions}</i></span>
          ) : null}
        </span>
        <span className="turn-artifacts-action review-summary-action">
          <span>审查</span>
          <ChevronRight size={14} />
        </span>
      </button>

      <div className="turn-artifacts-file-strip turn-artifacts-files" aria-label="Changed files list">
        {files.map((file) => (
          <button
            type="button"
            key={file.path}
            onClick={(event) => {
              event.stopPropagation();
              openReview(file.path);
            }}
            title={`在审查中打开 ${file.displayPath}`}
          >
            <FileCode2 size={13.5} strokeWidth={1.8} />
            <code title={file.path}>{file.displayPath}</code>
            {(file.additions || file.deletions) ? (
              <small><b>+{file.additions}</b><i>-{file.deletions}</i></small>
            ) : null}
          </button>
        ))}
      </div>

      {previewFile && workspace ? (
        <button
          type="button"
          className="turn-artifacts-preview-action"
          onClick={(event) => {
            event.stopPropagation();
            window.dispatchEvent(new CustomEvent("loom:artifact-preview-open", {
              detail: { path: previewFile.path, workspace },
            }));
          }}
          title={`渲染预览 ${previewFile.displayPath}`}
        >
          <ExternalLink size={13.5} strokeWidth={1.8} aria-hidden="true" />
          <span>渲染 {previewFile.name}</span>
          <ChevronRight size={13} aria-hidden="true" />
        </button>
      ) : null}

      <button
        type="button"
        className="review-summary-footer"
        onClick={(event) => {
          event.stopPropagation();
          openReview(primary.path);
        }}
      >
        <span>点击文件可在右侧审查栏查看完整代码差异</span>
        <ChevronRight size={13} />
      </button>
    </section>
  );
}
