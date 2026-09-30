import { createServer, type Server } from "node:http";
import { randomBytes, timingSafeEqual } from "node:crypto";

type Search = (query: string, count: number) => Promise<unknown>;

/** Loopback bridge from Python to main-process account auth. No provider key. */
export async function createSearchRelay(search: Search): Promise<{
  server: Server;
  env: Record<string, string>;
}> {
  const token = randomBytes(32).toString("hex");
  const server = createServer(async (req, res) => {
    const respond = (status: number, value: unknown) => {
      res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      res.end(JSON.stringify(value));
    };
    const given = Buffer.from(String(req.headers.authorization || ""));
    const expected = Buffer.from(`Bearer ${token}`);
    if (req.headers.origin || given.length !== expected.length || !timingSafeEqual(given, expected)) {
      req.resume();
      return respond(401, { error: { code: "MISSING_TOKEN", message: "Search bridge authentication required." } });
    }
    if (req.method !== "POST" || req.url !== "/search") {
      req.resume();
      return respond(404, { error: { code: "NOT_FOUND", message: "Endpoint not found." } });
    }
    try {
      let bytes = 0;
      const chunks: Buffer[] = [];
      for await (const chunk of req) {
        bytes += chunk.length;
        if (bytes > 8192) {
          respond(413, { error: { code: "REQUEST_TOO_LARGE", message: "Search request is too large." } });
          req.resume();
          return;
        }
        chunks.push(Buffer.from(chunk));
      }
      let body;
      try { body = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
      catch { return respond(400, { error: { code: "INVALID_JSON", message: "Invalid search request." } }); }
      if (!body || typeof body.query !== "string" || !body.query.trim() || body.query.length > 400
          || !Number.isInteger(body.count) || body.count < 1 || body.count > 20) {
        return respond(400, { error: { code: "INVALID_SEARCH", message: "Invalid search query or count." } });
      }
      respond(200, await search(body.query, body.count));
    } catch (cause) {
      const error = cause as { status?: number; code?: string; message?: string };
      respond(error.status && error.status >= 400 && error.status <= 599 ? error.status : 502,
        { error: { code: error.code || "SEARCH_UNAVAILABLE", message: error.code ? error.message : "Shared search is unavailable." } });
    }
  });
  server.requestTimeout = 20_000;
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  server.unref();
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Search bridge failed to bind.");
  return { server, env: { LOOM_SEARCH_RELAY_URL: `http://127.0.0.1:${address.port}/search`, LOOM_SEARCH_RELAY_TOKEN: token } };
}
