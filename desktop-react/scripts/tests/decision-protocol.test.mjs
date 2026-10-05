import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";

const source = buildSync({
  entryPoints: [fileURLToPath(new URL("../../src/components/DecisionPromptCard.tsx", import.meta.url))],
  bundle: true, platform: "node", format: "esm", write: false, loader: { ".css": "empty" },
}).outputFiles[0].text;
const { parseDecisionMessage } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const cases = JSON.parse(readFileSync(new URL("../../../tests/fixtures/decision_protocol.json", import.meta.url), "utf8"));

for (const fixture of cases) {
  test(`shared decision protocol: ${fixture.name}`, () => {
    const parsed = parseDecisionMessage(fixture.text);
    assert.equal(parsed.incomplete, !fixture.valid);
    assert.equal(parsed.decisions.length, fixture.decisions);
    if (!fixture.decisions && fixture.valid) assert.equal(parsed.text, fixture.text);
  });
}
