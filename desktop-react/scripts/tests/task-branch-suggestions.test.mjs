import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { test } from "node:test";
import ts from "typescript";

const root = path.resolve(import.meta.dirname, "../..");
const parserPath = path.join(root, "src/components/TaskBranchCard.tsx");
const parserSource = fs.readFileSync(parserPath, "utf8");
const ast = ts.createSourceFile(parserPath, parserSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const method = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === "parseTaskBranchProposal");
assert.ok(method, "task proposal parser must exist");

const compiled = ts.transpileModule(
  method.getText(ast) + "\nglobalThis.parseProposal = parseTaskBranchProposal;\n",
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } },
).outputText;
const context = {};
vm.runInNewContext(compiled, context);
const parse = context.parseProposal;

function candidate(payload) {
  const fence = "\x60".repeat(3);
  return "Answer completed.\n\n" + fence + "loom-task\n" + JSON.stringify(payload) + "\n" + fence + "\n";
}
const plan = {
  actionable: true,
  title: "Fix CRLF tests",
  summary: "Keep Windows and Linux editor tests passing.",
  prompt: "Inspect Files editor tests, fix CRLF assumptions, run both checks.",
  acceptanceCriteria: ["CRLF test passes", "LF test passes"],
};

test("ordinary assistant answers never create suggested tasks", () => {
  assert.equal(parse("This is a short explanation.").proposal, null);
  assert.equal(parse("Thanks for your help!").proposal, null);
});

test("old-format or invalid model suggestions never become cards", () => {
  for (const bad of [
    { title: "Fix", prompt: "Do it" },
    { ...plan, actionable: false },
    { ...plan, acceptanceCriteria: [] },
    { ...plan, title: "x".repeat(90) },
    { ...plan, summary: "" },
  ]) {
    const result = parse(candidate(bad));
    assert.equal(result.proposal, null);
    assert.equal(result.text, "Answer completed.");
  }
});

test("a concrete opt-in proposal includes its checks", () => {
  const result = parse(candidate(plan));
  assert.equal(result.proposal?.title, "Fix CRLF tests");
  assert.deepEqual(Array.from(result.proposal?.acceptanceCriteria || []), plan.acceptanceCriteria);
  assert.equal(result.text, "Answer completed.");
});

test("manual child creation is an action, not an automatic per-message card", () => {
  const transcript = fs.readFileSync(path.join(root, "src/components/Transcript.tsx"), "utf8");
  assert.match(transcript, /const branchVisible = manualBranchOpen \|\| Boolean\(taskBranch\)/);
  assert.match(transcript, /onCreateSubtask=\{cardsReady/);
  assert.doesNotMatch(transcript, /className="loom-task-branch-new"/);
});
