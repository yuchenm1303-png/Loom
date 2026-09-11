import {
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronRight as ExpandIcon,
  Copy,
  ExternalLink,
  FileCode2,
  FileDiff,
  WrapText,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { TranscriptItem } from "../types/loom";
import "./turn-artifacts-preview.css";

type DiffRow =
  | { id: string; kind: "fold"; count: number }
  | { id: string; kind: "hunk"; text: string }
  | { id: string; kind: "meta"; text: string }
  | { id: string; kind: "context" | "add" | "delete"; text: string; oldLine?: number; newLine?: number };

type DiffRowInput = DiffRow extends infer Row
  ? Row extends { id: string }
    ? Omit<Row, "id">
    : never
  : never;

type DiffFile = {
  path: string;
  displayPath: string;
  name: string;
  diff: string;
  rows: DiffRow[];
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

function parseDiff(diff: string): { rows: DiffRow[]; additions: number; deletions: number } {
  const rows: DiffRow[] = [];
  const lines = String(diff || "").replace(/\r\n/g, "\n").split("\n");
  let oldLine = 0;
  let newLine = 0;
  let seenHunk = false;
  let additions = 0;
  let deletions = 0;
  let rowIndex = 0;

  const push = (row: DiffRowInput) => {
    rows.push({ ...row, id: `inline-diff-${rowIndex++}` } as DiffRow);
  };

  for (const raw of lines) {
    const hunk = /^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$/.exec(raw);
    if (hunk) {
      const nextOld = Number(hunk[1]);
      const nextNew = Number(hunk[3]);
      const hidden = seenHunk
        ? Math.max(0, Math.max(nextOld - oldLine, nextNew - newLine))
        : Math.max(0, Math.max(nextOld - 1, nextNew - 1));
      if (hidden > 0) push({ kind: "fold", count: hidden });
      push({ kind: "hunk", text: raw });
      oldLine = nextOld;
      newLine = nextNew;
      seenHunk = true;
      continue;
    }

    if (/^(diff --git |index |--- |\+\+\+ )/.test(raw)) continue;
    if (raw === "\\ No newline at end of file") {
      push({ kind: "meta", text: raw });
      continue;
    }

    if (seenHunk && raw.startsWith("+") && !raw.startsWith("+++")) {
      push({ kind: "add", text: raw.slice(1), newLine });
      newLine += 1;
      additions += 1;
      continue;
    }

    if (seenHunk && raw.startsWith("-") && !raw.startsWith("---")) {
      push({ kind: "delete", text: raw.slice(1), oldLine });
      oldLine += 1;
      deletions += 1;
      continue;
    }

    if (seenHunk && raw.startsWith(" ")) {
      push({ kind: "context", text: raw.slice(1), oldLine, newLine });
      oldLine += 1;
      newLine += 1;
      continue;
    }

    if (raw.trim()) push({ kind: "meta", text: raw });
  }

  return { rows, additions, deletions };
}

function collectFiles(items: TranscriptItem[]): DiffFile[] {
  const collected = new Map<string, { parts: string[]; editCount: number }>();

  for (const item of items) {
    if (item.type !== "file_edit") continue;
    const paths = (item.paths ?? []).map(normalizePath).filter(Boolean);
    const patches = splitPatch(String(item.diff ?? ""), paths);

    if (!patches.length && paths.length) {
      for (const path of paths) {
        const current = collected.get(path) ?? { parts: [], editCount: 0 };
        current.editCount += 1;
        collected.set(path, current);
      }
      continue;
    }

    for (const patch of patches) {
      const path = normalizePath(patch.path) || paths[0] || "Workspace change";
      const current = collected.get(path) ?? { parts: [], editCount: 0 };
      if (patch.diff.trim()) current.parts.push(patch.diff.trimEnd());
      current.editCount += 1;
      collected.set(path, current);
    }

    for (const path of paths) {
      if (!collected.has(path)) collected.set(path, { parts: [], editCount: 1 });
    }
  }

  return [...collected.entries()]
    .map(([path, value]) => {
      const diff = value.parts.join("\n");
      const parsed = parseDiff(diff);
      return {
        path,
        displayPath: compactPath(path),
        name: basename(path),
        diff,
        rows: parsed.rows,
        additions: parsed.additions,
        deletions: parsed.deletions,
        editCount: value.editCount,
      } satisfies DiffFile;
    })
    .sort((a, b) => a.displayPath.localeCompare(b.displayPath));
}

function openReview(path?: string): void {
  window.dispatchEvent(new CustomEvent("loom:review-open", {
    detail: path ? { path } : {},
  }));
}

function rowMarker(kind: DiffRow["kind"]): string {
  if (kind === "add") return "+";
  if (kind === "delete") return "−";
  return "";
}

export function TurnArtifactsPreview({ items }: { items: TranscriptItem[] }) {
  const files = useMemo(() => collectFiles(items), [items]);
  const [open, setOpen] = useState(false);
  const [selectedPath, setSelectedPath] = useState("");
  const [copied, setCopied] = useState(false);
  const [wrap, setWrap] = useState(false);

  const totals = useMemo(() => files.reduce(
    (total, file) => ({
      additions: total.additions + file.additions,
      deletions: total.deletions + file.deletions,
    }),
    { additions: 0, deletions: 0 },
  ), [files]);

  const selected = files.find((file) => file.path === selectedPath) ?? files[0];
  const selectedIndex = selected ? files.findIndex((file) => file.path === selected.path) : -1;

  useEffect(() => {
    if (!files.length) return;
    if (!selectedPath || !files.some((file) => file.path === selectedPath)) {
      setSelectedPath(files[0].path);
    }
  }, [files, selectedPath]);

  useEffect(() => {
    setCopied(false);
  }, [selected?.path]);

  if (!files.length) return null;

  const title = files.length === 1 ? `已编辑 ${files[0].name}` : `已修改 ${files.length} 个文件`;

  const moveSelection = (offset: number) => {
    if (!files.length) return;
    const current = selectedIndex < 0 ? 0 : selectedIndex;
    const next = Math.min(files.length - 1, Math.max(0, current + offset));
    setSelectedPath(files[next].path);
  };

  const copyPatch = async () => {
    if (!selected?.diff) return;
    try {
      await navigator.clipboard.writeText(selected.diff);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section className={`turn-artifacts rich-turn-artifacts ${open ? "is-open" : ""}`} aria-label="Changed files">
      <button
        type="button"
        className="turn-artifacts-header"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <span className="turn-artifacts-icon" aria-hidden="true"><FileDiff size={15} /></span>
        <span className="turn-artifacts-copy">
          <strong>{title}</strong>
          {(totals.additions || totals.deletions) ? (
            <span className="turn-artifacts-stats"><b>+{totals.additions}</b><i>-{totals.deletions}</i></span>
          ) : null}
        </span>
        <span className="turn-artifacts-action">
          <span>{open ? "收起" : "查看更改"}</span>
          <ExpandIcon size={14} />
        </span>
      </button>

      <div className="turn-artifacts-file-strip" aria-label="Changed files list">
        {files.map((file) => (
          <button
            type="button"
            className={selected?.path === file.path ? "active" : ""}
            key={file.path}
            onClick={() => {
              setSelectedPath(file.path);
              setOpen(true);
            }}
            title={file.path}
          >
            <FileCode2 size={13.5} strokeWidth={1.8} />
            <span>{file.displayPath}</span>
            {(file.additions || file.deletions) ? (
              <small><b>+{file.additions}</b><i>-{file.deletions}</i></small>
            ) : null}
          </button>
        ))}
      </div>

      <div className="turn-artifacts-grid">
        <div className="turn-artifacts-inner rich-turn-artifacts-inner">
          {selected ? (
            <>
              <div className="inline-diff-toolbar">
                <div className="inline-diff-file">
                  <FileCode2 size={14} strokeWidth={1.8} />
                  <span title={selected.path}>{selected.displayPath}</span>
                  <small>{selected.editCount > 1 ? `${selected.editCount} 次修改` : "1 次修改"}</small>
                </div>

                <div className="inline-diff-actions">
                  <div className="inline-diff-nav" aria-label="File navigation">
                    <button type="button" onClick={() => moveSelection(-1)} disabled={selectedIndex <= 0} title="上一个文件">
                      <ChevronLeft size={13} />
                    </button>
                    <span>{selectedIndex + 1}/{files.length}</span>
                    <button type="button" onClick={() => moveSelection(1)} disabled={selectedIndex >= files.length - 1} title="下一个文件">
                      <ChevronRight size={13} />
                    </button>
                  </div>

                  <button
                    type="button"
                    className={`inline-diff-tool ${wrap ? "active" : ""}`}
                    onClick={() => setWrap((value) => !value)}
                    title={wrap ? "关闭自动换行" : "自动换行"}
                    aria-pressed={wrap}
                  >
                    <WrapText size={13} />
                    <span>换行</span>
                  </button>

                  <button
                    type="button"
                    className={`inline-diff-tool ${copied ? "active" : ""}`}
                    onClick={() => void copyPatch()}
                    disabled={!selected.diff}
                    title="复制当前文件补丁"
                  >
                    {copied ? <Check size={13} /> : <Copy size={13} />}
                    <span>{copied ? "已复制" : "复制"}</span>
                  </button>

                  <button
                    type="button"
                    className="inline-diff-tool primary"
                    onClick={() => openReview(selected.path)}
                    title="在右侧审查面板打开"
                  >
                    <ExternalLink size={13} />
                    <span>审查</span>
                  </button>
                </div>
              </div>

              {selected.rows.length ? (
                <div className={`inline-diff-scroll ${wrap ? "wrap" : ""}`}>
                  <div className="inline-diff-table" role="table" aria-label={selected.path}>
                    {selected.rows.map((row) => {
                      if (row.kind === "fold") {
                        return (
                          <div className="inline-diff-fold" key={row.id}>
                            <span>{row.count} 行未修改</span>
                          </div>
                        );
                      }
                      if (row.kind === "hunk") {
                        return <div className="inline-diff-hunk" key={row.id}>{row.text}</div>;
                      }
                      if (row.kind === "meta") {
                        return <div className="inline-diff-meta" key={row.id}>{row.text}</div>;
                      }
                      return (
                        <div className={`inline-diff-row is-${row.kind}`} key={row.id} role="row">
                          <span className="inline-diff-number">{row.oldLine ?? ""}</span>
                          <span className="inline-diff-number">{row.newLine ?? ""}</span>
                          <span className="inline-diff-marker">{rowMarker(row.kind)}</span>
                          <code>{row.text || " "}</code>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <div className="inline-diff-empty">
                  <FileDiff size={18} />
                  <strong>这个文件没有附带可解析的代码差异</strong>
                  <span>可以在右侧审查面板查看完整文件修改记录。</span>
                  <button type="button" onClick={() => openReview(selected.path)}>打开审查</button>
                </div>
              )}
            </>
          ) : null}
        </div>
      </div>
    </section>
  );
}
