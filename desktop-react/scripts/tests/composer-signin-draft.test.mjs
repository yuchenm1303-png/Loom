import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

test("signed-out submit opens login without clearing or sending the draft", async () => {
  const text = readFileSync(new URL("../../src/components/ComposerBase.tsx", import.meta.url), "utf8");
  const source = ts.createSourceFile("ComposerBase.tsx", text, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX);
  let submit;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === "submit") submit = node.getText(source);
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(submit);
  const code = ts.transpileModule(submit, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  let opened = 0;
  const fail = () => { throw new Error("Signed-out draft must remain untouched"); };
  const invoke = new Function("value", "quote", "attachments", "imagesAllowed", "disabled", "running", "accountAuthenticated", "onOpenAccount", "formatQuotedPrompt", "setValue", "setQuote", "setAttachments", "onSend", `${code}; return submit;`)(
    "draft", null, [], true, false, false, false, () => opened++, (_quote, input) => input, fail, fail, fail, fail);
  await invoke();
  assert.equal(opened, 1);
});
