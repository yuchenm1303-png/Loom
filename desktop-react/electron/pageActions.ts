import fs from "node:fs/promises";

/**
 * The browser extension's runPageAction, as source text. Chrome serializes that
 * function for executeScript, so it is self-contained by construction; Loom
 * Browser runs the same text in its frames and reads pages exactly as the
 * extension does (labels, toggle states, shadow roots, frames, repeated names).
 */
export function extractRunPageAction(source: string): string {
  // A Windows checkout has CRLF line ends.
  const text = source.replace(/\r\n/g, "\n");
  const start = text.indexOf("\nfunction runPageAction(");
  if (start < 0) throw new Error("runPageAction is missing from the browser extension.");
  const end = text.indexOf("\n}\n", start + 1);
  if (end < 0) throw new Error("runPageAction has no end in the browser extension.");
  return text.slice(start + 1, end + 2);
}

export async function loadRunPageAction(backgroundScript: string): Promise<string> {
  return extractRunPageAction(await fs.readFile(backgroundScript, "utf8"));
}
