import { useId, useMemo, useState, type CSSProperties } from "react";
import { artifactRenderer, canRenderArtifact, type ArtifactRendererKind } from "../artifactRenderers";
import type { TranscriptItem } from "../types/loom";
import {
  ChevronDown, ChevronRight, Eye, File, FileCode2, FileDiff, FileImage, FileText, Film, Globe2, Music,
  type IconComponent,
} from "./icons";
import { useRuntimeCopy } from "./runtimeCopy";
import { collectFiles, type DiffFile } from "./turnChanges";
import "./turn-artifacts-preview.css";

/** A list longer than this keeps its largest changes in view and folds the rest behind one row. */
const VISIBLE_FILES = 6;

const FILE_GLYPHS: Record<ArtifactRendererKind, IconComponent> = {
  web: Globe2,
  image: FileImage,
  pdf: FileText,
  video: Film,
  audio: Music,
  text: FileText,
  code: FileCode2,
  data: FileCode2,
  file: File,
};

function openReview(path?: string): void {
  window.dispatchEvent(new CustomEvent("loom:review-open", {
    detail: path ? { path } : {},
  }));
}

function count(value: number): string {
  return value.toLocaleString("en-US");
}

/** Zero sides stay out of the way: "+4" rather than "+4 -0". */
function DiffStats({ additions, deletions }: { additions: number; deletions: number }) {
  if (!additions && !deletions) return <span className="tc-stats" />;
  return (
    <span className="tc-stats">
      {additions ? <span className="tc-add">+{count(additions)}</span> : null}
      {deletions ? <span className="tc-del">-{count(deletions)}</span> : null}
    </span>
  );
}

/**
 * How big the change is and what it is made of: the track is the same width on every row, the fill is this file's
 * share of the largest change in the turn, split into what was added and what was removed.
 */
function DiffBar({ additions, deletions, share, index }: { additions: number; deletions: number; share: number; index: number }) {
  if (!additions && !deletions) return <span className="tc-bar-slot" />;
  return (
    <span className="tc-bar" aria-hidden="true">
      <span className="tc-bar-fill" style={{ width: `${Math.round(Math.max(.1, Math.min(1, share)) * 100)}%`, "--tc-i": Math.min(index, 4) } as CSSProperties}>
        {additions ? <i className="tc-bar-add" style={{ flexGrow: additions }} /> : null}
        {deletions ? <i className="tc-bar-del" style={{ flexGrow: deletions }} /> : null}
      </span>
    </span>
  );
}

/** The folder, with a left-to-right mark at each end so the row can cut it from the left (CSS direction) without the dot or slash jumping sides. */
function folder(dir: string): string {
  return dir ? `\u200e${dir}\u200e` : "";
}

function FileRow({ file, share, index, label }: { file: DiffFile; share: number; index: number; label: string }) {
  const Glyph = FILE_GLYPHS[artifactRenderer(file.path).kind];
  return (
    <li>
      <button
        type="button"
        className="tc-file"
        aria-label={label}
        onClick={(event) => {
          event.stopPropagation();
          openReview(file.path);
        }}
      >
        <Glyph className="tc-file-glyph" size={15} strokeWidth={1.7} aria-hidden="true" />
        <span className="tc-file-text">
          <span className="tc-file-name">{file.name}</span>
          <span className="tc-file-dir" title={file.dir ? file.displayPath : undefined}>{folder(file.dir)}</span>
        </span>
        <DiffStats additions={file.additions} deletions={file.deletions} />
        <DiffBar additions={file.additions} deletions={file.deletions} share={share} index={index + 1} />
      </button>
    </li>
  );
}

export function TurnArtifactsPreview({ items, workspace }: { items: TranscriptItem[]; workspace?: string }) {
  const copy = useRuntimeCopy();
  const files = useMemo(() => collectFiles(items), [items]);
  const totals = useMemo(() => files.reduce(
    (total, file) => ({
      additions: total.additions + file.additions,
      deletions: total.deletions + file.deletions,
    }),
    { additions: 0, deletions: 0 },
  ), [files]);
  const [expanded, setExpanded] = useState(false);
  const moreId = useId();

  if (!files.length) return null;

  const primary = files[0];
  const single = files.length === 1;
  // "Created" or "Deleted" only when that is all the turn did; one file of each kind among others is "Changed".
  const kind = files.every((file) => file.kind === primary.kind) ? primary.kind : "edited";
  const largest = Math.max(1, ...files.map((file) => file.additions + file.deletions));
  // The file the turn touched last is the one worth previewing.
  const previewFile = files
    .filter((file) => canRenderArtifact(file.path))
    .sort((a, b) => b.lastChange - a.lastChange)[0] ?? null;
  const folded = files.length > VISIBLE_FILES + 1;
  const shown = folded ? files.slice(0, VISIBLE_FILES) : files;
  const hidden = folded ? files.slice(VISIBLE_FILES) : [];
  const row = (file: DiffFile, index: number) => (
    <FileRow key={file.path} file={file} share={(file.additions + file.deletions) / largest} index={index} label={copy.openInReview(file.displayPath)} />
  );

  return (
    <section className="tc" aria-label={copy.changedFilesRegion} data-single={single ? "true" : undefined}>
      <div className="tc-head">
        <button
          type="button"
          className="tc-open"
          onClick={(event) => {
            event.stopPropagation();
            openReview(primary.path);
          }}
        >
          <span className="tc-mark" aria-hidden="true"><FileDiff size={17} strokeWidth={1.8} /></span>
          <span className="tc-title">
            <span className="tc-line">
              <span className="tc-lead">{copy.changedFilesLead(files.length, kind)}</span>
              <strong className="tc-subject">{copy.changedFilesSubject(files.length, primary.name)}</strong>
            </span>
            {single && primary.dir ? <span className="tc-dir" title={primary.displayPath}>{folder(primary.dir)}</span> : null}
          </span>
        </button>

        <span className="tc-figures">
          <DiffStats additions={totals.additions} deletions={totals.deletions} />
          <DiffBar additions={totals.additions} deletions={totals.deletions} share={1} index={0} />
        </span>

        <span className="tc-actions">
          {previewFile && workspace ? (
            <button
              type="button"
              className="tc-action tc-preview"
              aria-label={copy.previewArtifactTitle(previewFile.displayPath)}
              onClick={(event) => {
                event.stopPropagation();
                window.dispatchEvent(new CustomEvent("loom:artifact-preview-open", {
                  detail: { path: previewFile.path, workspace },
                }));
              }}
            >
              <Eye size={14} strokeWidth={1.8} aria-hidden="true" />
              <span>{copy.preview}</span>
              {single ? null : <span className="tc-action-name">{previewFile.name}</span>}
            </button>
          ) : null}
          <button
            type="button"
            className="tc-action tc-review"
            onClick={(event) => {
              event.stopPropagation();
              openReview(primary.path);
            }}
          >
            <span>{copy.review}</span>
            <ChevronRight size={14} aria-hidden="true" />
          </button>
        </span>
      </div>

      {single ? null : (
        <div className="tc-body">
          <ul className="tc-files" aria-label={copy.changedFilesRegion}>
            {shown.map((file, index) => row(file, index))}
          </ul>
          {hidden.length ? (
            <>
              <div className="tc-more" data-open={expanded ? "true" : "false"} inert={!expanded}>
                <div className="tc-more-inner">
                  <ul className="tc-files" id={moreId}>
                    {hidden.map((file, index) => row(file, shown.length + index))}
                  </ul>
                </div>
              </div>
              <button
                type="button"
                className="tc-toggle"
                aria-expanded={expanded}
                aria-controls={moreId}
                onClick={(event) => {
                  event.stopPropagation();
                  setExpanded((value) => !value);
                }}
              >
                <ChevronDown size={14} aria-hidden="true" />
                <span>{expanded ? copy.fewerChangedFiles : copy.moreChangedFiles(hidden.length)}</span>
              </button>
            </>
          ) : null}
        </div>
      )}
    </section>
  );
}
