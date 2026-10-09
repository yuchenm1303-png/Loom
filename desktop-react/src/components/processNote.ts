/**
 * Process notes are the model's running commentary between tool rows. They stay
 * in document order and keep their full text, but read as quiet captions.
 *
 * This is a display-density rule only: it never judges whether a note matters,
 * so a long note is clamped and expandable, never dropped or reclassified.
 */
const NOTE_LONG_CHARS = 420;
const NOTE_LONG_LINE_BREAKS = 6;

export function isLongNote(text: string): boolean {
  const value = String(text ?? "").trim();
  if (value.length > NOTE_LONG_CHARS) return true;
  let breaks = 0;
  for (let index = value.indexOf("\n"); index !== -1; index = value.indexOf("\n", index + 1)) {
    breaks += 1;
    if (breaks >= NOTE_LONG_LINE_BREAKS) return true;
  }
  return false;
}
