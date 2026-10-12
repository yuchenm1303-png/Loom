// Mutable raw GitHub URLs can lag behind the repository even with no-store.
// Prefer the repository API; keep raw as a fallback for API rate limits.
export async function readUpdateChannel(
  url: string,
  fetchBytes: (url: string, limit: number) => Promise<Buffer>,
  limit: number,
): Promise<unknown> {
  const parsed = new URL(url);
  if (parsed.hostname === "raw.githubusercontent.com" && parsed.pathname.startsWith("/yuchenm1303-png/Loom/main/")) {
    const file = parsed.pathname.slice("/yuchenm1303-png/Loom/main/".length);
    try {
      const bytes = await fetchBytes(`https://api.github.com/repos/yuchenm1303-png/Loom/contents/${file}?ref=main&check=${Date.now()}`, limit * 2);
      const response = JSON.parse(bytes.toString("utf8"));
      if (response.encoding !== "base64" || typeof response.content !== "string") throw new Error("Invalid update channel API response");
      const decoded = Buffer.from(response.content, "base64");
      if (decoded.length > limit) throw new Error("Update channel response too large");
      return JSON.parse(decoded.toString("utf8"));
    } catch {
      // The raw endpoint remains usable when the unauthenticated API is limited.
    }
  }
  parsed.searchParams.set("check", String(Date.now()));
  return JSON.parse((await fetchBytes(parsed.href, limit)).toString("utf8"));
}
