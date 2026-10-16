import { app } from "electron";
import fs from "node:fs";
import path from "node:path";

export function recordUpdateAccessDiagnostic(details: {
  endpoint?: "/auth/me" | "/auth/refresh";
  reason?: string;
  elapsedMs: number;
  code?: string;
  status?: number;
  transportCode?: string;
}) {
  try {
    const code = (value: unknown) => typeof value === "string" && /^[A-Z0-9_]{1,80}$/.test(value) ? value : "UNKNOWN";
    const file = path.join(app.getPath("userData"), "update-access-diagnostics.jsonl");
    if (fs.existsSync(file) && fs.statSync(file).size > 65536) {
      const tail = fs.readFileSync(file, "utf8").slice(-32768);
      fs.writeFileSync(file, tail.slice(tail.indexOf("\n") + 1));
    }
    fs.appendFileSync(file, JSON.stringify({ at: new Date().toISOString(),
      endpoint: details.endpoint,
      reason: details.reason && /^[a-z-]{1,40}$/.test(details.reason) ? details.reason : undefined,
      elapsedMs: details.elapsedMs, code: code(details.code), status: details.status ?? 0,
      transportCode: code(details.transportCode),
    }) + "\n", { mode: 0o600 });
  } catch { /* Diagnostic failures must not affect authentication. */ }
}
