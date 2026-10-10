const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });

const SENTENCE_BREAK = /[。！？!?；;\n]/u;
const SOFT_BREAK = /[，,：:\s]/u;

// Parsing a growing Markdown table is substantially more expensive than prose.
// Batch long messages without dropping canonical text or slowing catch-up.
export function streamingFrameInterval(length: number): number {
  return length >= 12000 ? 120 : length >= 4000 ? 80 : 28;
}

// Graphemes per second with nothing waiting, and the most a paint may catch up at.
const BASE_RATE = 78;
const FINAL_RATE = 112;
const MAX_RATE = 330;
const MAX_FINAL_RATE = 560;
// The tempo follows the backlog continuously: it would drain in about this long. Stepped gears (the old
// thresholds at 72, 180, 480 and 1200 graphemes) shifted the typing speed by a third at a time, which
// reads as a stutter in the rhythm of the text.
const DRAIN_SECONDS = 2;

function paintBudget(remaining: number, elapsedMs: number, finalizing: boolean): number {
  const elapsed = Math.min(96, Math.max(12, Number.isFinite(elapsedMs) ? elapsedMs : 32));
  const rate = Math.min(
    finalizing ? MAX_FINAL_RATE : MAX_RATE,
    (finalizing ? FINAL_RATE : BASE_RATE) + remaining / DRAIN_SECONDS,
  );
  const budget = Math.round((rate * elapsed) / 1000);
  const catchupFloor = remaining > 420 ? 4 : remaining > 160 ? 2 : 1;
  return Math.min(finalizing ? 28 : 18, Math.max(catchupFloor, budget));
}

/**
 * Advance the presentation by a time-based grapheme budget.
 *
 * Provider chunks may arrive one token at a time or in large bursts. The UI
 * should not inherit that transport cadence, so the visual budget is based on
 * elapsed presentation time and backlog pressure instead. Sentence/word
 * boundaries are preferred once enough progress was made in this paint.
 */
export function advanceStreamingText(
  current: string,
  target: string,
  elapsedMs = 32,
  finalizing = false,
  finishWithinMs = Infinity,
): string {
  if (!target.startsWith(current)) return target;
  if (current === target) return target;
  if (finalizing && finishWithinMs <= elapsedMs) return target;

  const remaining = target.length - current.length;
  const batches = streamingFrameInterval(target.length) > 28
    ? Math.max(1, Math.min(4, Math.floor(elapsedMs / 28)))
    : 1;
  const normalBudget = paintBudget(remaining, elapsedMs / batches, finalizing) * batches;
  // Spread a final provider burst over the remaining handoff window instead
  // of revealing a few glyphs and dumping the entire backlog at its deadline.
  const deadlineBudget = finalizing && Number.isFinite(finishWithinMs)
    ? Math.ceil(remaining * Math.min(1, Math.max(12, elapsedMs) / Math.max(1, finishWithinMs)))
    : 0;
  const budget = Math.max(normalBudget, deadlineBudget);
  const softBoundaryFloor = Math.max(3, Math.floor(budget * 0.72));

  let end = current.length;
  let count = 0;
  for (const part of segmenter.segment(target.slice(end))) {
    end += part.segment.length;
    count += 1;
    if (count >= budget) break;
    if (count >= Math.max(2 * batches, deadlineBudget) && SENTENCE_BREAK.test(part.segment)) break;
    if (count >= softBoundaryFloor && SOFT_BREAK.test(part.segment)) break;
  }
  return target.slice(0, end);
}

export function streamingGraphemes(value: string): string[] {
  return Array.from(segmenter.segment(value), (part) => part.segment);
}

const FENCE_LINE = /^ {0,3}(`{3,}|~{3,})(.*)$/;
const PARTIAL_FENCE_LINE = /^ {0,3}(`{1,2}|~{1,2})$/;
const BARE_BLOCK_MARKER = /^ {0,3}(#{1,6}|[-*+]|\d{1,9}[.)]|>)\s*$/;
const TABLE_DELIMITER = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

type Span = [number, number];

/** Close (or drop) the code span left open at the end of `block`. */
function healCodeSpans(block: string): { text: string; spans: Span[] } {
  const spans: Span[] = [];
  let open = -1;
  let openLength = 0;
  for (let index = 0; index < block.length;) {
    if (block[index] !== "`") {
      index += 1;
      continue;
    }
    let end = index;
    while (block[end] === "`") end += 1;
    const length = end - index;
    if (open < 0) {
      open = index;
      openLength = length;
    } else if (length === openLength) {
      spans.push([open, end]);
      open = -1;
    }
    index = end;
  }
  if (open < 0) return { text: block, spans };
  // An opener with nothing after it yet is held back; one with content is
  // closed, so the chip grows with its text instead of a stray backtick.
  if (!block.slice(open + openLength).trim()) return { text: block.slice(0, open), spans };
  spans.push([open, block.length + openLength]);
  return { text: `${block}${"`".repeat(openLength)}`, spans };
}

/** Close a `**` or `~~` delimiter left open outside code spans. */
function healDelimiter(block: string, marker: "**" | "~~", inCode: (index: number) => boolean): string {
  const positions: number[] = [];
  for (let index = block.indexOf(marker); index >= 0; index = block.indexOf(marker, index + marker.length)) {
    if (!inCode(index)) positions.push(index);
  }
  if (positions.length % 2 === 0) return block;
  const last = positions[positions.length - 1];
  const content = block.slice(last + marker.length);
  if (!content.trim()) return block.slice(0, last);
  // A closing delimiter may not follow whitespace; keep the space outside it.
  const trailing = content.match(/\s*$/)?.[0] ?? "";
  return `${block.slice(0, block.length - trailing.length)}${marker}${trailing}`;
}

/** A link or image still arriving at the end shows its label (links) or nothing (images). */
function healTrailingLink(block: string, inCode: (index: number) => boolean): string {
  const bracket = block.lastIndexOf("[");
  if (bracket < 0 || inCode(bracket)) return block;
  // Sticker control markers ("[[AI_LEDGER_...") have their own parser.
  if (block[bracket - 1] === "[" || block[bracket + 1] === "[") return block;
  const tail = block.slice(bracket);
  const image = block[bracket - 1] === "!";
  const start = image ? bracket - 1 : bracket;
  const label = /^\[([^\]\n]*)(\]\([^)\s]*)?$/.exec(tail);
  if (!label) return block;
  // "items[0" is an index being typed, not a link label.
  if (!label[2] && !image && /[A-Za-z0-9_\])]/.test(block[bracket - 1] ?? "")) return block;
  return `${block.slice(0, start)}${image ? "" : label[1]}`;
}

/**
 * The visible prefix of a streaming Markdown message, made safe to render.
 *
 * Text can stop anywhere: inside an inline code span, between two bold
 * markers, after a fence's opening backticks, halfway through a link target.
 * Rendering that prefix verbatim flashes raw syntax ("`validators", "**粗"),
 * an empty code block labelled "code", or a pipe-separated paragraph that
 * becomes a table a moment later. This closes what is open at the growth edge
 * and holds back syntax whose meaning is not settled yet. Only presentation
 * changes; canonical text is untouched and a finished message never passes
 * through here.
 */
export function healStreamingMarkdown(text: string): string {
  if (!text) return text;
  const lines = text.split("\n");
  let fenceMarker = "";
  let fenceOpenedAt = -1;
  let blockStart = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const fence = FENCE_LINE.exec(line);
    if (fence) {
      if (!fenceMarker) {
        fenceMarker = fence[1];
        fenceOpenedAt = index;
      } else if (fence[1][0] === fenceMarker[0] && fence[1].length >= fenceMarker.length && !fence[2].trim()) {
        fenceMarker = "";
        fenceOpenedAt = -1;
        blockStart = index + 1;
      }
      continue;
    }
    if (!fenceMarker && !line.trim()) blockStart = index + 1;
  }

  const last = lines.length - 1;
  if (fenceMarker) {
    // The opening fence is still being typed: its language is unknown and
    // there is no code yet. Hold the line back instead of an empty block.
    if (fenceOpenedAt === last) return lines.slice(0, last).join("\n");
    // A closing fence half typed would show as a line of backticks in the code.
    if (PARTIAL_FENCE_LINE.test(lines[last])) return lines.slice(0, last).join("\n");
    return text;
  }

  const head = lines.slice(0, blockStart).join("\n");
  let blockLines = lines.slice(blockStart);
  // An opening fence whose backticks are still arriving ("`", "``").
  if (PARTIAL_FENCE_LINE.test(blockLines[blockLines.length - 1] ?? "")) blockLines = blockLines.slice(0, -1);
  // A heading, list or quote marker with no words after it yet.
  if (BARE_BLOCK_MARKER.test(blockLines[blockLines.length - 1] ?? "")) blockLines = blockLines.slice(0, -1);
  // Pipes become a table only once the delimiter row arrives.
  if (blockLines.length && blockLines.every((line) => line.trimStart().startsWith("|"))
    && !blockLines.slice(1).some((line) => TABLE_DELIMITER.test(line))) {
    blockLines = [];
  }
  if (!blockLines.length) return head.replace(/\n+$/, "");

  const code = healCodeSpans(blockLines.join("\n"));
  const inCode = (index: number) => code.spans.some(([start, end]) => index >= start && index < end);
  let block = healTrailingLink(code.text, inCode);
  block = healDelimiter(block, "**", inCode);
  block = healDelimiter(block, "~~", inCode);
  return head ? `${head}\n${block}` : block;
}
