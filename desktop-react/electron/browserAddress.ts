export function normalizeBrowserAddress(value: string): string {
  const text = String(value || "").trim();
  if (!text || text.length > 2048) throw new Error("Enter a valid address (up to 2048 characters).");
  if (/^(javascript|data|file|about|chrome|devtools|blob):/i.test(text)) {
    throw new Error("Only HTTP and HTTPS websites can be opened.");
  }
  let candidate = text;
  if (!/^[a-z][a-z\d+.-]*:\/\//i.test(text)) {
    if (/^[a-z][a-z\d+.-]*:/i.test(text) && !/^[\w.-]+:\d+(?:[/?#]|$)/i.test(text)) {
      throw new Error("Unsupported URL protocol.");
    }
    if (/\s/.test(text) || (!text.includes(".") && !/^localhost(?::\d+)?(?:[/?#]|$)/i.test(text))) {
      candidate = "https://www.google.com/search?q=" + encodeURIComponent(text);
    } else {
      candidate = (/^(localhost|127\.0\.0\.1)(?::|\/|$)/i.test(text) ? "http://" : "https://") + text;
    }
  }
  const parsed = new URL(candidate);
  if (!["http:", "https:"].includes(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password) {
    throw new Error("Only HTTP and HTTPS websites can be opened.");
  }
  return parsed.toString();
}
