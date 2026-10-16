/**
 * Split a unified diff into per-file chunks (path -> chunk text). Understands both shapes a diff arrives in: `diff --git`
 * sections, and the plain `---`/`+++` pairs the runtime's turn diff is made of (it never writes a `diff --git` line).
 */
export function splitDiffByFile(diff: string): Map<string, string> {
  const files = new Map<string, string>();
  const lines = diff.split("\n");
  let path = "";
  let chunk: string[] = [];
  let sawHunk = false;
  const flush = () => {
    if (chunk.length && path) files.set(path, chunk.join("\n"));
    chunk = [];
    sawHunk = false;
  };
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const git = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
    const pair = line.startsWith("--- ") && lines[index + 1]?.startsWith("+++ ");
    if (git || (pair && (sawHunk || !chunk.length || !path))) {
      if (!(pair && chunk.length && !sawHunk && path)) flush();
      if (git) path = git[2].trim();
    }
    if (pair) {
      const target = lines[index + 1].slice(4).trim().replace(/^b\//, "");
      const source = line.slice(4).trim().replace(/^a\//, "");
      path = target && target !== "/dev/null" ? target : source;
    }
    if (line.startsWith("@@")) sawHunk = true;
    chunk.push(line);
  }
  flush();
  return files;
}
