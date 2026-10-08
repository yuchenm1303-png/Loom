import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Test the executable shipped to users, rather than a Python environment or
// the build directory. Model selection must never prevent local startup.
export function verifyPackagedRuntime(resources) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "loom-runtime-gate-"));
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) =>
    !/^(LOOM_|OPENAI_|OPENCODE_|MINIMAX_|DASHSCOPE_|AI_API_KEY|PYTHONPATH|PYTHONHOME)/i.test(name)));
  const profiles = [
    ["openai", "gpt-4o", ""],
    ["openai-compatible", "AntAngelMed", "https://account.smirel.com/model/v1"],
    ["opencode-go", "muse-spark-1.3-contributor", "https://opencode.ai/zen/go/v1"],
  ];
  try {
    for (const [provider, model, base] of profiles) {
      const args = ["loom_app_server.py", "--home", home, "--workspace", home,
        "--provider", provider, "--model", model, "--allow-unconfigured-model"];
      if (base) args.push("--base-url", base);
      const frames = ["initialize", "thread/list"].map((method, index) =>
        ({ jsonrpc: "2.0", id: index + 1, method, params: method === "initialize" ? { protocolVersion: 1 } : { limit: 1 } }));
      const result = spawnSync(path.join(resources, "python.exe"), args, {
        cwd: resources, env: { ...env, LOOM_HOME: home, PYTHONUTF8: "1" },
        input: frames.map(frame => JSON.stringify(frame) + "\n").join(""),
        encoding: "utf8", windowsHide: true, timeout: 60_000, maxBuffer: 2 * 1024 * 1024,
      });
      if (result.error) throw result.error;
      assert.equal(result.status, 0, `${provider} packaged startup failed: ${result.stderr}`);
      const replies = result.stdout.split(/\r?\n/).filter(line => line.startsWith("{"))
        .map(line => JSON.parse(line));
      for (const frame of frames) assert.ok(replies.some(reply => reply.id === frame.id && "result" in reply),
        `${provider} did not complete ${frame.method}: ${result.stderr}`);
    }
    console.log("Packaged runtime verified: all executable provider adapters initialize and list local threads.");
  } finally { fs.rmSync(home, { recursive: true, force: true }); }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  verifyPackagedRuntime(path.resolve(process.argv[2] || fileURLToPath(new URL("../release/win-unpacked/resources", import.meta.url))));
}
