import { useState } from "react";
import { DiffView } from "../components/WeaveFlow";
import { useRuntimeCopy } from "../components/runtimeCopy";
import { folderText, type DiffFile } from "../components/turnChanges";
import { Disclosure } from "./Disclosure";

/** A list longer than this keeps its largest changes in view and folds the rest behind one row. */
const VISIBLE_FILES = 6;

function count(value: number): string {
  return value.toLocaleString("en-US");
}

function Stats({ additions, deletions }: { additions: number; deletions: number }) {
  if (!additions && !deletions) return null;
  return (
    <span className="m-changes-stats">
      {additions ? <span className="is-add">+{count(additions)}</span> : null}
      {deletions ? <span className="is-del">−{count(deletions)}</span> : null}
    </span>
  );
}

/** The chunk from its first hunk on: the row above it already names the file. Empty when there is no hunk to read. */
function hunksOf(diff: string): string {
  if (diff.startsWith("@@")) return diff;
  const hunk = diff.indexOf("\n@@");
  return hunk < 0 ? "" : diff.slice(hunk + 1);
}

function FileLabel({ file, lead }: { file: DiffFile; lead?: string }) {
  return (
    <span className="m-changes-label">
      <span className="m-changes-line">
        {lead ? <span className="m-changes-lead">{lead}</span> : null}
        <span className="m-changes-name">{file.name}</span>
      </span>
      {file.dir ? <span className="m-changes-dir">{folderText(file.dir)}</span> : null}
    </span>
  );
}

/** One file: its name, its folder, its size, and the diff itself under it on a tap. A file the turn recorded no hunks for has nothing to open. */
function FileRow({ file, lead }: { file: DiffFile; lead?: string }) {
  const hunks = hunksOf(file.diff);
  const stats = <Stats additions={file.additions} deletions={file.deletions} />;
  if (!hunks) return <div className="m-changes-static"><FileLabel file={file} lead={lead} />{stats}</div>;
  return (
    <Disclosure title={<FileLabel file={file} lead={lead} />} aside={stats}>
      <DiffView text={hunks} />
    </Disclosure>
  );
}

/**
 * The changed-files card on the phone. The desktop card's Preview and Review open panes the phone does not have, so
 * here the card answers the question itself: tapping a file unfolds what changed in it, in place.
 */
export function MobileTurnChanges({ files }: { files: DiffFile[] }) {
  const copy = useRuntimeCopy();
  const [expanded, setExpanded] = useState(false);
  const primary = files[0];
  const single = files.length === 1;
  const kind = files.every((file) => file.kind === primary.kind) ? primary.kind : "edited";
  const lead = copy.changedFilesLead(files.length, kind);
  const folded = files.length > VISIBLE_FILES + 1;
  const shown = folded ? files.slice(0, VISIBLE_FILES) : files;
  const hidden = folded ? files.slice(VISIBLE_FILES) : [];
  const additions = files.reduce((sum, file) => sum + file.additions, 0);
  const deletions = files.reduce((sum, file) => sum + file.deletions, 0);

  // The turn's own surface handles taps on what lies under it; a tap on this card stays here.
  return (
    <section className="m-changes" aria-label={copy.changedFilesRegion} onClick={(event) => event.stopPropagation()}>
      {single ? <FileRow file={primary} lead={lead} /> : (
        <>
          <header className="m-changes-head">
            <span className="m-changes-summary">{lead} {copy.changedFilesSubject(files.length, primary.name)}</span>
            <Stats additions={additions} deletions={deletions} />
          </header>
          {shown.map((file) => <FileRow key={file.path} file={file} />)}
          {hidden.length ? (
            <Disclosure
              className="m-changes-more"
              title={expanded ? copy.fewerChangedFiles : copy.moreChangedFiles(hidden.length)}
              open={expanded}
              onOpenChange={setExpanded}
            >
              {hidden.map((file) => <FileRow key={file.path} file={file} />)}
            </Disclosure>
          ) : null}
        </>
      )}
    </section>
  );
}
