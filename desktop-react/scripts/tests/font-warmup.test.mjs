import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const source = readFileSync(new URL("../../src/fontWarmup.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const { UI_FONT_WEIGHTS } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

function* files(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.name === "node_modules") continue;
    const path = join(directory, entry.name);
    if (entry.isDirectory()) yield* files(path);
    else if (/\.(css|tsx?)$/.test(entry.name)) yield path;
  }
}

test("every weight the source asks for is made while the app is idle", () => {
  const used = new Map();
  for (const path of files(fileURLToPath(new URL("../../src", import.meta.url)))) {
    const text = readFileSync(path, "utf8");
    for (const match of text.matchAll(/font-weight\s*:\s*(\d{3})\b|fontWeight\s*:\s*(\d{3})\b/g)) {
      const weight = Number(match[1] ?? match[2]);
      used.set(weight, path);
    }
  }
  assert.ok(used.size > 20, `the scan found the stylesheets' weights (${used.size})`);
  const missing = [...used.entries()].filter(([weight]) => !UI_FONT_WEIGHTS.includes(weight)).map(([weight, path]) => `${weight} (${path.split(/[\/]/).pop()})`);
  assert.deepEqual(missing, [], "add these to UI_FONT_WEIGHTS in src/fontWarmup.ts: each is a font instance made on the first frame that uses it");
  assert.deepEqual([...UI_FONT_WEIGHTS], [...new Set(UI_FONT_WEIGHTS)].sort((a, b) => a - b), "in order, without repeats");
});
