import { test } from "node:test";
import assert from "node:assert/strict";
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";
const bundle = buildSync({ entryPoints: [fileURLToPath(new URL("../../src/mobile/mobileProjectState.ts", import.meta.url))], bundle: true, write: false, format: "esm", platform: "node" });
const { inProject, searchThreads, buildStartParams, resumeBudgetParams } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`);
const project = { id: "p1", root: "C:/Projects/Loom/" };
const overview = { proposal: { id: "proposal-1" }, workspace: { status: "ready", changeCount: 0 } };
const settings = { tokens: "500000", episodes: "400", workers: "3", milestones: "20", fullAccess: false };

test("project ids take precedence; older workspace-only threads still match without confusing nested roots", () => {
  assert.equal(inProject({ projectId: "p1", workspace: "moved" }, project), true);
  assert.equal(inProject({ projectId: "p2", workspace: project.root }, project), false);
  assert.equal(inProject({ workspace: "c:\\projects\\LOOM" }, project), true);
  assert.equal(inProject({ workspace: "C:/Projects/Loom/nested" }, project), false);
  assert.equal(inProject({ workspace: "C:/Projects/Loom2" }, project), false);
  const threads = [{ id: "a", projectId: "p1", title: "修复登录", workspace: "C:/work" }, { id: "b", projectId: "p2", title: "修复登录", workspace: "C:/other" }];
  assert.deepEqual(searchThreads(threads, " 登录 ", project).map((item) => item.id), ["a"]);
});
test("starting a proposal forwards the edited settings, exact proposal id and attachments", () => {
  assert.deepEqual(buildStartParams("thread-1", overview, settings, ["C:/tmp/ref.pdf"]), {
    threadId: "thread-1", proposalId: "proposal-1", operationId: "weave:proposal-1", tokenBudget: 500000,
    maxEpisodes: 400, maxWorkers: 3, maxMilestones: 20, permissionMode: "workspace", attachments: ["C:/tmp/ref.pdf"],
  });
  assert.equal(buildStartParams("t", overview, { ...settings, fullAccess: true }, []).permissionMode, "full-access");
});
test("invalid budgets, out-of-range parallelism, dirty and unprepared workspaces cannot start", () => {
  for (const patch of [{ tokens: "" }, { tokens: "1.5" }, { episodes: "10001" }, { workers: "9" }, { workers: "0" }, { milestones: "51" }]) {
    assert.throws(() => buildStartParams("t", overview, { ...settings, ...patch }, []));
  }
  assert.throws(() => buildStartParams("t", { ...overview, proposal: null }, settings, []), /方案/);
  assert.throws(() => buildStartParams("t", { ...overview, workspace: { status: "not_git" } }, settings, []), /仓库/);
  assert.throws(() => buildStartParams("t", { ...overview, workspace: { status: "ready", changeCount: 1 } }, settings, []), /提交/);
});
test("resuming uses total stage budgets and accounts for the integration run's actual usage", () => {
  const run = { tokenBudget: 100, maxEpisodes: 10, tokensUsed: 90, episodesUsed: 8 };
  assert.deepEqual(resumeBudgetParams({ run }, "150", "10"), { tokenBudget: 150 });
  assert.throws(() => resumeBudgetParams({ run }, "80", "10"), /累计用量/);
  assert.throws(() => resumeBudgetParams({ run, integration: { ...run, tokensUsed: 120 } }, "100", "10"), /累计用量/);
});
