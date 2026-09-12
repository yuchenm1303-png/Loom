import {
  Check,
  ChevronLeft,
  ChevronRight,
  Copy,
  FileCode2,
  Files,
  Folder,
  Search,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import type { TranscriptItem } from "../types/loom";
import "./review-workspace.css";

type ReviewRow =
  | { id: string; kind: "fold"; count: number }
  | { id: string; kind: "hunk"; text: string }
  | { id: string; kind: "meta"; text: string }
  | { id: string; kind: "context" | "add" | "delete"; text: string; oldLine?: number; newLine?: number };

type WithoutId<T> = T extends { id: string } ? Omit<T, "id"> : never;
type ReviewRowInput = WithoutId<ReviewRow>;

type ReviewFile = {
  path: string;
  name: string;
  directory: string;
  diff: string;
  rows: ReviewRow[];
  additions: number;
  deletions: number;
  eventCount: number;
};

type ExternalReview = {
  items: TranscriptItem[];
  title?: string;
  subtitle?: string;
};

interface ReviewWorkspaceProps {
  items: TranscriptItem[];
  open: boolean;
  onClose(): void;
}

const COPY_TEXT = {
  en: {
    title: "Review changes",
    subtitle: "Inspect workspace edits recorded during this conversation.",
    files: "files",
    search: "Filter files…",
    current: "Current changes",
    empty: "No file edits are available to review.",
    noMatch: "No changed files match this filter.",
    noPatch: "This edit event reported the file path but did not include a unified diff.",
    unchanged: "unmodified lines",
    copy: "Copy patch",
    copied: "Copied",
    previous: "Previous file",
    next: "Next file",
    close: "Close review",
    event: "edit event",
    events: "edit events",
  },
  zh: {
    title: "审查更改",
    subtitle: "集中检查当前对话期间记录到的工作区代码修改。",
    files: "个文件",
    search: "筛选文件…",
    current: "当前更改",
    empty: "当前没有可审查的文件修改。",
    noMatch: "没有符合筛选条件的修改文件。",
    noPatch: "运行时记录了这个文件，但该修改事件没有附带 unified diff。",
    unchanged: "行未修改",
    copy: "复制补丁",
    copied: "已复制",
    previous: "上一个文件",
    next: "下一个文件",
    close: "关闭审查",
    event: "次修改",
    events: "次修改",
  },
} as const;

function normalizePath(value: string): string {
  return String(value || "")
    .trim()
    .replaceAll("\\", "/")
    .replace(/^[ab]\//, "")
    .replace(/^\.\//, "");
}

function fileName(path: string): string {
  const normalized = normalizePath(path);
  const parts = normalized.split("/").filter(Boolean);
  return parts.at(-1) || normalized || "Workspace change";
}

function directoryName(path: string): string {
  const normalized = normalizePath(path);
  const index = normalized.lastIndexOf("/");
  return index > 0 ? normalized.slice(0, index) : "Workspace";
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

function parseUnifiedDiff(diff: string): { rows: ReviewRow[]; additions: number; deletions: number } {
  const rows: ReviewRow[] = [];
  const lines = String(diff || "").replace(/\r\n/g, "\n").split("\n");
  let oldLine = 0;
  let newLine = 0;
  let seenHunk = false;
  let additions = 0;
  let deletions = 0;
  let rowIndex = 0;

  const push = (row: ReviewRowInput) => {
    rows.push({ ...row, id: `row-${rowIndex++}` } as ReviewRow);
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

function buildReviewFiles(items: TranscriptItem[]): ReviewFile[] {
  const collected = new Map<string, { parts: string[]; eventCount: number }>();

  for (const item of items) {
    if (item.type !== "file_edit") continue;
    const patches = splitPatch(String(item.diff || ""), item.paths ?? []);
    if (!patches.length && (item.paths ?? []).length) {
      for (const rawPath of item.paths ?? []) {
        const path = normalizePath(rawPath);
        if (!path) continue;
        const current = collected.get(path) ?? { parts: [], eventCount: 0 };
        current.eventCount += 1;
        collected.set(path, current);
      }
      continue;
    }
    for (const patch of patches) {
      const path = normalizePath(patch.path) || "Workspace change";
      const current = collected.get(path) ?? { parts: [], eventCount: 0 };
      if (patch.diff.trim()) current.parts.push(patch.diff.trimEnd());
      current.eventCount += 1;
      collected.set(path, current);
    }
  }

  return [...collected.entries()]
    .map(([path, value]) => {
      const diff = value.parts.join("\n");
      const parsed = parseUnifiedDiff(diff);
      return {
        path,
        name: fileName(path),
        directory: directoryName(path),
        diff,
        rows: parsed.rows,
        additions: parsed.additions,
        deletions: parsed.deletions,
        eventCount: value.eventCount,
      } satisfies ReviewFile;
    })
    .sort((a, b) => a.path.localeCompare(b.path));
}

function lineMarker(kind: ReviewRow["kind"]): string {
  if (kind === "add") return "+";
  if (kind === "delete") return "−";
  return "";
}

export function ReviewWorkspace({ items, open, onClose }: ReviewWorkspaceProps) {
  const { language } = useI18n();
  const c = language === "zh-CN" ? COPY_TEXT.zh : COPY_TEXT.en;
  const [externalReview, setExternalReview] = useState<ExternalReview | null>(null);
  const sourceItems = externalReview?.items ?? items;
  const files = useMemo(() => buildReviewFiles(sourceItems), [sourceItems]);
  const [query, setQuery] = useState("");
  const [selectedPath, setSelectedPath] = useState("");
  const [copied, setCopied] = useState(false);
  const reviewTitle = externalReview?.title || c.title;
  const reviewSubtitle = externalReview?.subtitle || c.subtitle;

  const visibleFiles = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return files;
    return files.filter((file) => file.path.toLowerCase().includes(needle));
  }, [files, query]);

  const selected = visibleFiles.find((file) => file.path === selectedPath)
    ?? visibleFiles[0]
    ?? files[0];

  const groups = useMemo(() => {
    const grouped = new Map<string, ReviewFile[]>();
    for (const file of visibleFiles) {
      const bucket = grouped.get(file.directory) ?? [];
      bucket.push(file);
      grouped.set(file.directory, bucket);
    }
    return [...grouped.entries()];
  }, [visibleFiles]);

  const totals = useMemo(() => files.reduce(
    (total, file) => ({
      additions: total.additions + file.additions,
      deletions: total.deletions + file.deletions,
    }),
    { additions: 0, deletions: 0 },
  ), [files]);

  const selectedIndex = selected ? visibleFiles.findIndex((file) => file.path === selected.path) : -1;

  useEffect(() => {
    const handleExternalReview = (event: Event) => {
      const detail = (event as CustomEvent<{
        path?: string;
        items?: TranscriptItem[];
        item?: TranscriptItem;
        title?: string;
        subtitle?: string;
      }>).detail;
      const nextItems = Array.isArray(detail?.items)
        ? detail.items
        : detail?.item
          ? [detail.item]
          : [];
      setExternalReview({
        items: nextItems,
        title: detail?.title,
        subtitle: detail?.subtitle,
      });
      setQuery("");
      const preferred = normalizePath(detail?.path || String(nextItems[0]?.paths?.[0] || ""));
      setSelectedPath(preferred);
    };

    const clearExternalReview = () => {
      setExternalReview(null);
      setQuery("");
      setSelectedPath("");
    };

    window.addEventListener("loom:review-open-diff", handleExternalReview);
    window.addEventListener("loom:review-clear-external", clearExternalReview);
    return () => {
      window.removeEventListener("loom:review-open-diff", handleExternalReview);
      window.removeEventListener("loom:review-clear-external", clearExternalReview);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    if (!selectedPath && files[0]) setSelectedPath(files[0].path);
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    document.documentElement.dataset.loomReviewOpen = "true";
    return () => {
      window.removeEventListener("keydown", handleKey);
      delete document.documentElement.dataset.loomReviewOpen;
    };
  }, [files, onClose, open, selectedPath]);

  useEffect(() => {
    if (!open || !visibleFiles.length) return;
    if (!visibleFiles.some((file) => file.path === selectedPath)) {
      setSelectedPath(visibleFiles[0].path);
    }
  }, [open, selectedPath, visibleFiles]);

  useEffect(() => {
    setCopied(false);
  }, [selected?.path]);

  if (!open) return null;

  const moveSelection = (offset: number) => {
    if (!visibleFiles.length) return;
    const current = selectedIndex < 0 ? 0 : selectedIndex;
    const next = Math.min(visibleFiles.length - 1, Math.max(0, current + offset));
    setSelectedPath(visibleFiles[next].path);
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

  return createPortal(
    <div className="review-workspace" role="dialog" aria-modal="true" aria-label={reviewTitle}>
      <header className="review-topbar">
        <div className="review-heading">
          <span className="review-heading-icon"><Files size={17} strokeWidth={1.8} /></span>
          <div>
            <strong>{reviewTitle}</strong>
            <span>{reviewSubtitle}</span>
          </div>
        </div>
        <div className="review-summary">
          <span>{files.length} {c.files}</span>
          <span className="review-additions">+{totals.additions}</span>
          <span className="review-deletions">−{totals.deletions}</span>
        </div>
        <button type="button" className="review-close" onClick={onClose} title={c.close} aria-label={c.close}>
          <X size={17} strokeWidth={1.8} />
        </button>
      </header>

      <div className="review-toolbar">
        <div className="review-round-label"><span>{c.current}</span><i /></div>
        <div className="review-file-navigation">
          <button type="button" onClick={() => moveSelection(-1)} disabled={selectedIndex <= 0} title={c.previous} aria-label={c.previous}>
            <ChevronLeft size={15} />
          </button>
          <span>{visibleFiles.length && selectedIndex >= 0 ? `${selectedIndex + 1} / ${visibleFiles.length}` : "0 / 0"}</span>
          <button type="button" onClick={() => moveSelection(1)} disabled={selectedIndex < 0 || selectedIndex >= visibleFiles.length - 1} title={c.next} aria-label={c.next}>
            <ChevronRight size={15} />
          </button>
        </div>
      </div>

      <div className="review-layout">
        <main className="review-diff-panel">
          {selected ? (
            <>
              <div className="review-file-header">
                <div className="review-file-title">
                  <FileCode2 size={16} strokeWidth={1.8} />
                  <strong title={selected.path}>{selected.path}</strong>
                  <span className="review-file-stats"><b>+{selected.additions}</b><em>−{selected.deletions}</em></span>
                </div>
                <div className="review-file-actions">
                  <span>{selected.eventCount} {selected.eventCount === 1 ? c.event : c.events}</span>
                  <button type="button" onClick={() => void copyPatch()} disabled={!selected.diff}>
                    {copied ? <Check size={13} /> : <Copy size={13} />}
                    {copied ? c.copied : c.copy}
                  </button>
                </div>
              </div>
              <div className="review-diff-scroll">
                {selected.rows.length ? (
                  <div className="review-diff-table" role="table" aria-label={selected.path}>
                    {selected.rows.map((row) => {
                      if (row.kind === "fold") {
                        return <div className="review-fold-row" key={row.id}><span>{row.count} {c.unchanged}</span></div>;
                      }
                      if (row.kind === "hunk") {
                        return <div className="review-hunk-row" key={row.id}><span>{row.text}</span></div>;
                      }
                      if (row.kind === "meta") {
                        return <div className="review-meta-row" key={row.id}><span>{row.text}</span></div>;
                      }
                      return (
                        <div className={`review-code-row is-${row.kind}`} key={row.id} role="row">
                          <span className="review-line-number old">{row.oldLine ?? ""}</span>
                          <span className="review-line-number next">{row.newLine ?? ""}</span>
                          <span className="review-line-marker">{lineMarker(row.kind)}</span>
                          <code>{row.text || " "}</code>
                        </div>
                      );
                    })}
                  </div>
                ) : <div className="review-empty-diff">{c.noPatch}</div>}
              </div>
            </>
          ) : <div className="review-empty-diff large">{files.length ? c.noMatch : c.empty}</div>}
        </main>

        <aside className="review-files-panel">
          <label className="review-file-search">
            <Search size={15} strokeWidth={1.8} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={c.search} />
            {query ? <button type="button" onClick={() => setQuery("")} aria-label="Clear filter"><X size={13} /></button> : null}
          </label>
          <div className="review-files-scroll">
            {!visibleFiles.length ? <div className="review-files-empty">{files.length ? c.noMatch : c.empty}</div> : null}
            {groups.map(([directory, directoryFiles]) => (
              <section className="review-file-group" key={directory}>
                <div className="review-directory-row"><Folder size={14} /><span title={directory}>{directory}</span><small>{directoryFiles.length}</small></div>
                <div className="review-directory-files">
                  {directoryFiles.map((file) => (
                    <button
                      type="button"
                      className={selected?.path === file.path ? "active" : ""}
                      key={file.path}
                      onClick={() => setSelectedPath(file.path)}
                      title={file.path}
                    >
                      <FileCode2 size={14} strokeWidth={1.8} />
                      <span>{file.name}</span>
                      <small className="review-tree-stats"><b>+{file.additions}</b><em>−{file.deletions}</em></small>
                    </button>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </aside>
      </div>
    </div>,
    document.body,
  );
}
