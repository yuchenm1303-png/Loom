/**
 * The work log of a turn, drawn as a thread.
 *
 * One stage of tool work is a run of steps on a thin thread. Each step is a node on it: the node says
 * what kind of step it is, and its state (running, done, failed, waiting) is how it looks, so the words
 * of a row never change when the step finishes and nothing reflows. A stage that is over folds into one
 * line of beads (one per step), and a live stage that has grown long keeps only its newest steps as rows
 * with the older ones folded into the same kind of line. Every appearance, fold and unfold below is the
 * same motion (weave.css owns all of it), so nothing competes.
 */
import { ChevronRight, CircleSlash, CircleX, Eye, FileDiff, Ban, Hourglass, Terminal, TriangleAlert, Wrench } from "lucide-react";
import {
  Fragment,
  memo,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { artifactName } from "../artifactRenderers";
import { useMotionPresence } from "../motion/useMotionPresence";
import type { TranscriptItem } from "../types/loom";
import {
  activitySegments,
  browserScreenshotPaths,
  buildActivityRows,
  describeRow,
  diffStats,
  groupSummary,
  identitySource,
  isActiveActivityStatus,
  isExecutingActivityStatus,
  isFailureStatus,
  liveHintKind,
  rowDetail,
  rowFailure,
  rowHasDetail,
  rowSpan,
  rowStatus,
  rowTail,
  rowTone,
  stageDuration,
  visualArtifactPath,
  type ActivityRowModel,
  type FileEditDelta,
  type StepTone,
} from "./activityModel";
import { foldedCount } from "./liveWindow";
import { isActivityItem } from "./executionSequence";
import { activityIdentity, ActivityGlyph as ToolIdentityGlyph } from "./ToolIdentity";
import { deferredActivityIndices } from "./presentationOrdering";
import { usePendingPresentations } from "./StreamingPresentation";
import { formatStepDuration } from "./stepFormat";
import { FileEditDeltaContext, LiveSequenceContext, ProcessNotesContext, useBornLive } from "./transcriptContexts";
import { useRuntimeCopy, type ActivityCategory, type RuntimeCopy } from "./runtimeCopy";
import "./weave.css";

/** Long enough for the folded state to finish its motion before anything is released. */
const FOLD_MS = 300;

// --- primitives -------------------------------------------------------------------------------

/**
 * The one height motion of the log. Content is mounted while it is open or still closing, and its height
 * is carried by a grid track (0fr <-> 1fr) so that what is below it glides instead of jumping. `animateIn`
 * is off for a fold that is already open when it first mounts (history plays nothing); once it has been
 * closed, every opening animates.
 */
export function Fold({ open, ms = FOLD_MS, animateIn = true, className = "", children }: {
  open: boolean;
  ms?: number;
  animateIn?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const presence = useMotionPresence(open, ms);
  const settledOnMount = useRef(!animateIn && open);
  if (!open) settledOnMount.current = false;
  if (!presence.mounted) return null;
  const shown = open && (settledOnMount.current || presence.phase !== "entering");
  return (
    <div className={`wv-fold ${className}`.trim()} data-open={shown ? "true" : "false"} inert={!open}>
      <div className="wv-fold-inner">{children}</div>
    </div>
  );
}

/** Re-renders once a second while `running`, and tells how long the step has been going. */
function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  return now;
}

/** A value that changes at most once per `ms`: a running command can print many lines a second. */
function useThrottled<T>(value: T, ms: number): T {
  const [shown, setShown] = useState(value);
  const latest = useRef(value);
  latest.current = value;
  useEffect(() => {
    if (value === shown) return;
    const timer = window.setTimeout(() => setShown(latest.current), ms);
    return () => window.clearTimeout(timer);
  }, [ms, shown, value]);
  return shown;
}

/** Beads for the steps of a stage or a whole turn: one per step, in order, coloured by how it went. */
export function Beads({ tones, max = 10, animated = false, labels }: {
  tones: readonly StepTone[];
  max?: number;
  animated?: boolean;
  labels?: readonly string[];
}) {
  const hidden = Math.max(0, tones.length - max);
  const shown = tones.slice(hidden);
  return (
    <span className="wv-beads" data-animate={animated ? "true" : undefined} aria-hidden="true">
      {hidden ? <b className="wv-beads-more">+{hidden}</b> : null}
      {shown.map((tone, index) => (
        <i key={hidden + index} data-tone={tone} title={labels?.[hidden + index]} style={{ "--i": Math.min(index, 12) } as CSSProperties} />
      ))}
    </span>
  );
}

// --- one step ---------------------------------------------------------------------------------

function KindGlyph({ row, category, size = 14 }: { row: ActivityRowModel; category: ActivityCategory; size?: number }) {
  if (category === "command") return <Terminal size={size} />;
  if (category === "edit") return <FileDiff size={size} />;
  const source = identitySource(row);
  const identity = activityIdentity(source);
  if (identity.family === "terminal") return <Terminal size={size} />;
  if (identity.family === "file") return <FileDiff size={size} />;
  if (identity.family === "generic") return <Wrench size={size} />;
  return <ToolIdentityGlyph item={source} size={size} />;
}

/** What the node shows: the kind of step while it is fine, its own mark when it is not. */
function NodeMark({ row, category, tone, status }: { row: ActivityRowModel; category: ActivityCategory; tone: StepTone; status: string }) {
  if (status === "denied") return <Ban size={14} />;
  if (tone === "failed") return <CircleX size={14} />;
  if (tone === "stopped") return <CircleSlash size={14} />;
  if (tone === "caution") return <TriangleAlert size={14} />;
  if (tone === "waiting") return <Hourglass size={14} />;
  return <KindGlyph row={row} category={category} />;
}

function BrowserScreenshotDetail({ paths, workspace }: { paths: string[]; workspace?: string }) {
  const copy = useRuntimeCopy();
  const pathKey = paths.join("\n");
  const [sources, setSources] = useState<Record<string, string>>({});
  const [failed, setFailed] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    let cancelled = false;
    setSources({});
    setFailed(new Set());
    const workspaceRoot = String(workspace ?? "").trim();
    if (!workspaceRoot) return () => { cancelled = true; };

    for (const path of paths) {
      void window.loom.readLocalImage(path, workspaceRoot)
        .then((result) => {
          if (cancelled) return;
          setSources((current) => ({ ...current, [path]: result.dataUrl }));
        })
        .catch(() => {
          if (cancelled) return;
          setFailed((current) => new Set(current).add(path));
        });
    }

    return () => { cancelled = true; };
  }, [pathKey, workspace]);

  return (
    <div className="wv-shots" aria-label={copy.imagesViewed(paths.length)}>
      {paths.map((path) => {
        const source = sources[path];
        const unavailable = failed.has(path) || !String(workspace ?? "").trim();
        const name = path.replaceAll("\\", "/").split("/").pop() || "browser-screenshot.png";
        return (
          <figure className="wv-shot" key={path} title={path}>
            {source ? (
              <img src={source} alt={copy.imageAlt(name)} loading="lazy" decoding="async" />
            ) : (
              <div className={`wv-shot-wait ${unavailable ? "is-unavailable" : ""}`}>
                {unavailable ? copy.imageUnavailable : copy.imageLoading}
              </div>
            )}
            <figcaption>{name}</figcaption>
          </figure>
        );
      })}
    </div>
  );
}

const DIFF_LINE_CAP = 500;

/** A diff with its additions and removals coloured, which is most of what reading one is. */
function DiffView({ text }: { text: string }) {
  const lines = text.split("\n");
  const shown = lines.length > DIFF_LINE_CAP ? lines.slice(0, DIFF_LINE_CAP) : lines;
  return (
    <pre className="wv-sheet-output is-diff">
      {shown.map((line, index) => {
        const kind = line.startsWith("@@") ? "hunk"
          : /^(diff --git|index |--- |\+\+\+ )/.test(line) ? "meta"
            : line.startsWith("+") ? "add"
              : line.startsWith("-") ? "del" : "";
        return <span className={`wv-diff-line ${kind ? `is-${kind}` : ""}`.trim()} key={index}>{line || " "}</span>;
      })}
      {lines.length > DIFF_LINE_CAP ? <span className="wv-diff-line is-meta">… {lines.length - DIFF_LINE_CAP}</span> : null}
    </pre>
  );
}

/** A path as its folder (quiet) and its file name (the thing). */
function PathText({ path }: { path: string }) {
  const clean = path.replace(/\/+$/, "");
  const cut = clean.lastIndexOf("/");
  if (cut < 0) return <>{path}</>;
  return (
    <>
      <span className="wv-dir">{clean.slice(0, cut + 1)}</span>
      <span className="wv-base">{clean.slice(cut + 1)}{path.endsWith("/") ? "/" : ""}</span>
    </>
  );
}

interface WeaveRowProps {
  row: ActivityRowModel;
  open: boolean;
  workspace?: string;
  copy: RuntimeCopy;
  delta?: FileEditDelta;
  /** Rows that were folded into the stage's earlier line while it is live: they collapse in place. */
  retired?: boolean;
  /** Mounted by opening a fold: history, so nothing is born. */
  still?: boolean;
  onToggle(id: string): void;
}

function sameRowProps(previous: WeaveRowProps, next: WeaveRowProps): boolean {
  if (previous.open !== next.open || previous.retired !== next.retired || previous.still !== next.still) return false;
  if (previous.workspace !== next.workspace || previous.copy !== next.copy || previous.delta !== next.delta) return false;
  if (previous.row.key !== next.row.key) return false;
  if (previous.row.item.id !== next.row.item.id || previous.row.item.type !== next.row.item.type) return false;
  if ((previous.row.wrapper?.id ?? "") !== (next.row.wrapper?.id ?? "")) return false;

  const previousStatus = rowStatus(previous.row);
  const nextStatus = rowStatus(next.row);
  if (previousStatus !== nextStatus) return false;

  if (previous.row.item.toolName !== next.row.item.toolName) return false;
  if (browserScreenshotPaths(previous.row.item).join("\n") !== browserScreenshotPaths(next.row.item).join("\n")) return false;
  if ((previous.row.item.paths ?? []).join("\n") !== (next.row.item.paths ?? []).join("\n")) return false;
  // The clock starts at a step's creation. Its end only matters once it is over: while it runs, updatedAt moves with
  // every output delta (many a second) and says nothing on screen, so it must not re-render the row.
  if (previous.row.item.createdAt !== next.row.item.createdAt || previous.row.wrapper?.createdAt !== next.row.wrapper?.createdAt) return false;
  if (!isActiveActivityStatus(nextStatus)
    && (previous.row.item.updatedAt !== next.row.item.updatedAt || previous.row.wrapper?.updatedAt !== next.row.wrapper?.updatedAt)) return false;
  if (previous.row.wrapper && next.row.wrapper && previous.row.wrapper.arguments !== next.row.wrapper.arguments) return false;
  if (previous.row.item.arguments !== next.row.item.arguments && !isActiveActivityStatus(nextStatus)) return false;
  // The command can finish streaming its arguments after the row exists.
  if (describeRow(previous.row, previous.copy).target !== describeRow(next.row, next.copy).target) return false;
  // What a running command last printed is on the row; the rest of its output is not until it is opened.
  if (rowTail(previous.row) !== rowTail(next.row)) return false;

  // Collapsed rows intentionally ignore stdout/stderr/content/argument deltas. Those can arrive every
  // presentation frame and used to make the row reconcile while its entrance was still running. When
  // expanded, the detail panel remains fully live.
  if (next.open) return rowDetail(previous.row, previous.delta) === rowDetail(next.row, next.delta);

  const active = isActiveActivityStatus(nextStatus);
  if (!active && rowHasDetail(previous.row) !== rowHasDetail(next.row)) return false;
  if (!active && next.row.item.type === "file_edit" && previous.row.item.diff !== next.row.item.diff) return false;
  return rowFailure(previous.row) === rowFailure(next.row);
}

const WeaveRow = memo(function WeaveRow({ row, open, workspace, copy, delta, retired = false, still = false, onToggle }: WeaveRowProps) {
  const status = rowStatus(row);
  const tone = rowTone(row);
  const active = isActiveActivityStatus(status);
  const executing = isExecutingActivityStatus(status);
  const born = useBornLive(row.key) && !still;
  // A step that finishes while the reader watches confirms once, in its outcome colour. The marker lives on
  // the row, so nothing else can replay it.
  const live = useContext(LiveSequenceContext);
  const previousStatusRef = useRef(status);
  const [settled, setSettled] = useState<"done" | "failed" | null>(null);
  useLayoutEffect(() => {
    const previous = previousStatusRef.current;
    previousStatusRef.current = status;
    if (!live || previous === status || !isActiveActivityStatus(previous) || isActiveActivityStatus(status)) return;
    setSettled(isFailureStatus(status) ? "failed" : "done");
  }, [live, status]);
  useEffect(() => {
    if (!settled) return;
    const timer = window.setTimeout(() => setSettled(null), 760);
    return () => window.clearTimeout(timer);
  }, [settled]);

  const expandable = active || rowHasDetail(row);
  const detailPresence = useMotionPresence(open, 260);
  const cachedDetailRef = useRef("");
  const liveDetail = open ? rowDetail(row, delta) : "";
  if (open) cachedDetailRef.current = liveDetail;
  const visibleDetail = open ? liveDetail : cachedDetailRef.current;
  const description = describeRow(row, copy, delta);
  const stats = row.item.type === "file_edit" && (!active || open)
    ? (delta ? { added: delta.added, removed: delta.removed } : diffStats(row.item.diff))
    : null;
  const screenshotPaths = browserScreenshotPaths(row.item);
  const hintPresence = useMotionPresence(open && active && !visibleDetail && !screenshotPaths.length, 200);
  const identity = activityIdentity(identitySource(row));
  const kind = description.category === "command" ? "process" : description.category === "edit" ? "file_edit" : row.item.type;
  // A page, image or document being written can be watched while it grows.
  const previewPath = description.category === "edit" && workspace && !active
    ? visualArtifactPath(row.item.type === "file_edit" ? (delta?.paths ?? row.item.paths ?? []) : [])
    : "";

  // Timing: a running step counts up, a finished one says how long it took.
  const span = rowSpan(row);
  const now = useNow(executing);
  const elapsedMs = span.start === null ? 0 : executing ? now - span.start : active ? 0 : (span.end ?? span.start) - span.start;
  const time = formatStepDuration(elapsedMs);

  // Under the row: what a running command is printing, or why a step failed.
  const tailNow = executing ? rowTail(row) : "";
  const tail = useThrottled(tailNow, 220);
  const failure = rowFailure(row);
  const sub = executing ? (tail || tailNow) : failure;
  const subKind = executing ? "tail" : "failure";
  const animate = live && !still;
  const subPresence = useMotionPresence(Boolean(sub), 220);
  const cachedSubRef = useRef({ text: "", kind: subKind });
  if (sub) cachedSubRef.current = { text: sub, kind: subKind };
  const subShown = sub ? { text: sub, kind: subKind } : cachedSubRef.current;

  const tagged = tone === "failed" || tone === "stopped" || tone === "caution" || tone === "waiting";
  const glyphKey = tagged ? status : "kind";
  const reviewable = description.category === "edit" || description.category === "read";

  return (
    <div
      className={`wv-step ${open ? "is-open" : ""} ${previewPath ? "has-preview" : ""}`.replace(/\s+/g, " ").trim()}
      data-kind={kind}
      data-tone={tone}
      data-status={status}
      data-row-key={row.key}
      data-born={born ? "live" : undefined}
      data-settled={settled ?? undefined}
      data-retired={retired ? "true" : undefined}
      inert={retired}
    >
      <div className="wv-step-inner">
        <button
          type="button"
          className={`wv-row ${expandable ? "is-expandable" : "no-detail"} ${active ? "is-active" : "is-resting"} ${executing ? "is-executing" : ""}`.replace(/\s+/g, " ").trim()}
          data-tool-family={description.category === "command" ? "terminal" : description.category === "edit" ? "file" : identity.family}
          onClick={() => expandable && onToggle(row.key)}
          aria-expanded={expandable ? open : undefined}
          aria-busy={executing || undefined}
          disabled={!expandable}
          title={expandable ? (open ? copy.collapseDetails : copy.expandDetails) : undefined}
        >
          <span className="wv-node" aria-hidden="true" title={identity.label}>
            <span className="wv-halo" />
            <span className="wv-glyph" key={glyphKey}><NodeMark row={row} category={description.category} tone={tone} status={status} /></span>
          </span>
          <span className="wv-main">
            {/* The node says how the step is going to the eye; this says it to everything else. */}
            <span className="sr-only">{copy.statusLabel(status)}</span>
            <span className="wv-verb" title={description.verb}>{description.label}</span>
            <span
              className={`wv-target ${description.code ? "is-code" : ""} ${description.path ? "is-path" : ""} ${reviewable ? "wv-path" : ""}`.replace(/\s+/g, " ").trim()}
              title={description.title}
            >
              {description.path ? <PathText path={description.path} /> : description.target}
            </span>
          </span>
          <span className="wv-meta">
            {stats && (stats.added > 0 || stats.removed > 0) ? (
              <span className="wv-diffstat">
                {stats.added ? <span className="wv-plus">+{stats.added}</span> : null}
                {stats.removed ? <span className="wv-minus">-{stats.removed}</span> : null}
              </span>
            ) : null}
            {tagged ? <span className="wv-tag" key={status}>{copy.statusLabel(status)}</span> : null}
            {time ? <span className="wv-time">{time}</span> : null}
          </span>
          <span className="wv-chevron-cell" aria-hidden="true"><ChevronRight size={12} className="wv-chevron" /></span>
        </button>

        {previewPath ? (
          <button
            type="button"
            className="wv-preview"
            onClick={() => window.dispatchEvent(new CustomEvent("loom:artifact-preview-open", { detail: { path: previewPath, workspace } }))}
            title={copy.previewArtifactTitle(previewPath)}
            aria-label={copy.previewArtifact(artifactName(previewPath))}
          >
            <Eye size={12} strokeWidth={1.9} aria-hidden="true" />
            <span>{copy.preview}</span>
          </button>
        ) : null}

        {subPresence.mounted ? (
          <div className="wv-sub-fold" data-open={sub && (!animate || subPresence.phase !== "entering") ? "true" : "false"} inert>
            <div className="wv-sub-inner">
              <div className={`wv-sub is-${subShown.kind}`}>
                <span className="wv-sub-text" key={subShown.text}>{subShown.text}</span>
              </div>
            </div>
          </div>
        ) : null}

        {expandable ? (
          <div className="wv-detail" data-open={open && detailPresence.phase !== "entering" ? "true" : "false"} data-motion-phase={detailPresence.phase} inert={!open}>
            <div className="wv-detail-inner">
              {detailPresence.mounted ? (
                <div className="wv-sheet">
                  {description.category === "command" && description.title ? (
                    <div className="wv-sheet-command"><span aria-hidden="true">$</span><code>{description.title}</code></div>
                  ) : null}
                  {screenshotPaths.length ? (
                    <BrowserScreenshotDetail paths={screenshotPaths} workspace={workspace} />
                  ) : visibleDetail ? (
                    row.item.type === "file_edit" ? <DiffView text={visibleDetail} /> : <pre className="wv-sheet-output">{visibleDetail}</pre>
                  ) : null}
                  {hintPresence.mounted ? (
                    <div className="wv-hint" data-open={hintPresence.phase === "exiting" ? "false" : "true"} role="status">
                      <span className="wv-hint-glow" aria-hidden="true" />
                      <span>{copy.liveHint(liveHintKind(row, status))}</span>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}, sameRowProps);

// --- lines: a folded stage, or the older steps of a live one ------------------------------------

interface LineProps {
  tones: readonly StepTone[];
  labels?: readonly string[];
  text: string;
  failed: number;
  duration: number;
  open: boolean;
  controls: string;
  animated: boolean;
  copy: RuntimeCopy;
  onToggle(): void;
}

function Line({ tones, labels, text, failed, duration, open, controls, animated, copy, onToggle }: LineProps) {
  const time = formatStepDuration(duration);
  return (
    <button
      type="button"
      className={`wv-line ${open ? "is-open" : ""}`.trim()}
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
      title={open ? copy.collapseDetails : copy.expandDetails}
    >
      <span className="wv-line-lead" aria-hidden="true"><ChevronRight size={12} className="wv-chevron" /></span>
      <Beads tones={tones} labels={labels} animated={animated} />
      <span className="wv-line-text" key={text}>{text}</span>
      {failed ? <span className="wv-line-failed">{copy.clusterFailed(failed)}</span> : null}
      {time ? <span className="wv-time wv-line-time">{time}</span> : null}
    </button>
  );
}

// --- the stage --------------------------------------------------------------------------------

export type ProcessHandoff = { retained: ReadonlySet<string>; folding: ReadonlySet<string> };

export interface WeaveStageProps {
  items: TranscriptItem[];
  /** The stage still has work running, or is the live anchor between two steps. */
  keepOpen?: boolean;
  /** A later stage exists in this live turn: this one is over, so it is one line unless opened. */
  superseded?: boolean;
  /** The sequence is the live layout of a turn (an active run, or one holding before it folds). */
  live?: boolean;
  workspace?: string;
  handoff?: ProcessHandoff;
  /** What the model said between tool steps, when the reader asked for it. */
  renderNote(item: TranscriptItem, latest: boolean): ReactNode;
}

function sameItemReferences(previous: readonly TranscriptItem[] | undefined, next: readonly TranscriptItem[]): boolean {
  if (!previous || previous.length !== next.length) return false;
  for (let index = 0; index < next.length; index += 1) {
    if (previous[index] !== next[index]) return false;
  }
  return true;
}

/** Grouping rebuilds the array on every delta; the stage only changes with its items. */
function sameStageProps(previous: WeaveStageProps, next: WeaveStageProps): boolean {
  return previous.keepOpen === next.keepOpen
    && previous.superseded === next.superseded
    && previous.live === next.live
    && previous.workspace === next.workspace
    && previous.handoff === next.handoff
    && previous.renderNote === next.renderNote
    && sameItemReferences(previous.items, next.items);
}

export const WeaveStage = memo(function WeaveStage({
  items,
  keepOpen = false,
  superseded = false,
  live = false,
  workspace,
  handoff,
  renderNote,
}: WeaveStageProps) {
  const copy = useRuntimeCopy();
  const deltas = useContext(FileEditDeltaContext);
  const notes = useContext(ProcessNotesContext);
  const rows = useMemo(() => buildActivityRows(items), [items]);
  // Per-step narration stays out of the log unless asked for. Hidden narration is simply not rendered, so
  // neighbouring steps form one run.
  const shownRows = useMemo(() => rows.filter((row) => row.item.type !== "assistant_message" || notes.show), [rows, notes.show]);
  const segments = useMemo(() => activitySegments(shownRows), [shownRows]);
  const pending = usePendingPresentations();
  const revealed = useRef(new Set<string>());
  const orderingBlocks = useMemo(() => segments.map((segment) => isActivityItem(segment[0].item)
    ? { kind: "activity" as const, items: segment.map((row) => ({ id: row.key })) }
    : { kind: "item" as const, item: segment[0].item }), [segments]);
  const deferred = deferredActivityIndices(orderingBlocks, pending, revealed.current);
  useLayoutEffect(() => {
    segments.forEach((segment, index) => {
      if (!deferred.has(index)) segment.forEach((row) => revealed.current.add(row.key));
    });
  }, [segments, pending]);

  const toolRows = useMemo(() => rows.filter((row) => isActivityItem(row.item)), [rows]);
  const tones = useMemo(() => toolRows.map(rowTone), [toolRows]);
  const failedSteps = useMemo(() => toolRows.reduce((count, row) => count + (isFailureStatus(rowStatus(row)) ? 1 : 0), 0), [toolRows]);

  // A stage that is over (a later one exists in the live layout) is one line, and so is a stage the reader has
  // closed. The latest stage and any still running stay open, and a click overrides until it runs again.
  const collapsible = superseded && toolRows.length >= 2;
  const [chosen, setChosen] = useState<boolean | null>(null);
  const open = collapsible ? (chosen ?? keepOpen) : true;
  const wasKeepingOpen = useRef(false);
  useEffect(() => {
    if (keepOpen && !wasKeepingOpen.current) setChosen(null);
    wasKeepingOpen.current = keepOpen;
  }, [keepOpen]);
  const [openRows, setOpenRows] = useState<Set<string>>(() => new Set());
  const toggleRow = useCallback((id: string) => {
    setOpenRows((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // A live stage that has grown long keeps its newest steps as rows. The older ones become one line.
  const windowed = live && !superseded && segments.length === 1 && isActivityItem(segments[0][0].item);
  const folded = windowed ? foldedCount(segments[0].length) : 0;
  const [settledFold, setSettledFold] = useState(0);
  useEffect(() => {
    if (folded <= settledFold) return;
    // The rows that just folded collapse in place; once that motion is over they are released.
    const timer = window.setTimeout(() => setSettledFold(folded), FOLD_MS + 40);
    return () => window.clearTimeout(timer);
  }, [folded, settledFold]);
  const [earlierOpen, setEarlierOpen] = useState(false);
  // For a moment after the reader opens a line its rows arrive one after another (weave.css), never on mount.
  const [opening, setOpening] = useState(false);
  useEffect(() => {
    if (!opening) return;
    const timer = window.setTimeout(() => setOpening(false), 800);
    return () => window.clearTimeout(timer);
  }, [opening]);
  // With the earlier line open, a row that folds simply moves into it, where it already would be.
  const releasedFold = earlierOpen ? folded : Math.min(settledFold, folded);
  const bodyId = useId();
  const earlierId = useId();

  const stageSummary = useMemo(() => groupSummary(toolRows, copy), [toolRows, copy]);
  const earlierRows = useMemo(() => (folded ? segments[0].slice(0, folded) : []), [folded, segments]);
  const earlierSummary = useMemo(() => groupSummary(earlierRows, copy), [earlierRows, copy]);
  const earlierTones = useMemo(() => earlierRows.map(rowTone), [earlierRows]);
  const earlierFailed = useMemo(() => earlierRows.reduce((count, row) => count + (isFailureStatus(rowStatus(row)) ? 1 : 0), 0), [earlierRows]);
  const labels = useMemo(() => toolRows.map((row) => {
    const description = describeRow(row, copy, deltas.get(row.item.id));
    return `${description.label} ${description.target}`.trim();
  }), [toolRows, copy, deltas]);

  const latestNote = useMemo(() => {
    for (let index = shownRows.length - 1; index >= 0; index -= 1) {
      if (shownRows[index].item.type === "assistant_message") return shownRows[index].item;
    }
    return undefined;
  }, [shownRows]);

  const envelope = (segment: ActivityRowModel[], content: ReactNode) => {
    const row = segment[0];
    const ids = [...new Set(segment.flatMap((entry) => [entry.key, entry.item.id]))];
    return handoff ? (
      <div key={row.key} className="process-handoff-slot" data-process-items={ids.join(" ")}
        data-handoff-phase={ids.every((id) => handoff.folding.has(id)) ? "folding"
          : ids.some((id) => handoff.retained.has(id)) ? "holding" : "current"}
        inert={ids.every((id) => handoff.folding.has(id))}>
        <div className="process-handoff-slot-inner">{content}</div>
      </div>
    ) : <Fragment key={row.key}>{content}</Fragment>;
  };

  const renderRow = (row: ActivityRowModel, extra: { retired?: boolean; still?: boolean } = {}) => (
    <WeaveRow
      key={row.key}
      row={row}
      open={openRows.has(row.key)}
      workspace={workspace}
      copy={copy}
      delta={row.item.type === "file_edit" ? deltas.get(row.item.id) : undefined}
      retired={extra.retired}
      still={extra.still}
      onToggle={toggleRow}
    />
  );

  const renderTools = (segment: ActivityRowModel[]) => (
    <div className="wv-list">
      {folded ? (
        <div className="wv-earlier" key="earlier"><div className="wv-earlier-inner">
          <Line
            tones={earlierTones}
            labels={labels.slice(0, folded)}
            text={earlierSummary}
            failed={earlierFailed}
            duration={0}
            open={earlierOpen}
            controls={earlierId}
            animated={live}
            copy={copy}
            onToggle={() => {
              setOpening(!earlierOpen);
              setEarlierOpen(!earlierOpen);
            }}
          />
          <div id={earlierId}>
            <Fold open={earlierOpen}>
              <div className="wv-list is-nested">{earlierRows.map((row) => renderRow(row, { still: true }))}</div>
            </Fold>
          </div>
        </div></div>
      ) : null}
      {segment.map((row, index) => {
        // Rows that folded while the reader watched collapse where they are; once released they are gone.
        if (windowed && index < releasedFold) return null;
        return renderRow(row, { retired: windowed && index < folded });
      })}
    </div>
  );

  const body = (
    <div className="wv-list">
      {segments.map((segment, index) => {
        if (deferred.has(index)) return null;
        const isNote = segment[0].item.type === "assistant_message";
        const content = isNote
          ? <div className={`wv-note ${segment[0].item.id === latestNote?.id ? "is-latest" : ""}`.trim()} key={segment[0].item.id}>{renderNote(segment[0].item, segment[0].item.id === latestNote?.id)}</div>
          : renderTools(segment);
        return envelope(segment, content);
      })}
    </div>
  );

  return (
    <section
      className={`wv-stage ${collapsible ? "is-collapsible" : ""} ${open ? "is-open" : ""} ${keepOpen ? "is-running" : ""}`.replace(/\s+/g, " ").trim()}
      data-opening={opening ? "true" : undefined}
      aria-label={copy.activityRegion}
    >
      {collapsible ? (
        <Fold open className="wv-summary-fold" animateIn={live}>
          <Line
            tones={tones}
            labels={labels}
            text={stageSummary}
            failed={failedSteps}
            duration={stageDuration(toolRows)}
            open={open}
            controls={bodyId}
            animated={live}
            copy={copy}
            onToggle={() => {
              setOpening(!open);
              setChosen(!open);
            }}
          />
        </Fold>
      ) : null}
      <div id={bodyId} className="wv-body-slot">
        <Fold open={open} animateIn={false}>{body}</Fold>
      </div>
    </section>
  );
}, sameStageProps);
