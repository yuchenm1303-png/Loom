import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/state/projectRead.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
const { withProjectReadTimeout } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("stalled project reads release the UI and ignore late results", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let reply;
  let applied = false;
  const read = withProjectReadTimeout(new Promise(resolve => { reply = resolve; }));
  const rejected = assert.rejects(read.then(() => { applied = true; }), /项目读取超时/);
  t.mock.timers.tick(20_000);
  await rejected;
  reply("late project");
  await Promise.resolve();
  assert.equal(applied, false);
  assert.equal(await withProjectReadTimeout(Promise.resolve("retry")), "retry");
});

test("project read preserves host errors", async () => {
  const failure = new Error("Host disconnected");
  await assert.rejects(withProjectReadTimeout(Promise.reject(failure)), error => error === failure);
});

function repositoryHarness(client, details = false) {
  const source = readFileSync(new URL(`../../src/components/${details === "memory" ? "ProjectMemoryCard" : details === "files" ? "FilesWorkbench" : details ? "ProjectDetailsPanel" : "ProjectRepositoryCard"}.tsx`, import.meta.url), "utf8");
  const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const slots = [], effects = [];
  let cursor = 0, lastTree;
  const react = {
    useRef(value) { const index = cursor++; return slots[index] ??= { current: value }; },
    useState(value) { const index = cursor++; if (!(index in slots)) slots[index] = value; return [slots[index], next => { slots[index] = typeof next === "function" ? next(slots[index]) : next; }]; },
    useCallback(fn, deps) {
      const index = cursor++;
      const old = slots[index];
      if (!old || deps.some((dep, i) => dep !== old.deps[i])) slots[index] = { deps, fn };
      return slots[index].fn;
    },
    useMemo: fn => fn(),
    useEffect(fn, deps) {
      const index = cursor++;
      const old = slots[index];
      if (!old || !deps || deps.some((dep, i) => dep !== old.deps[i])) effects.push(() => {
        old?.cleanup?.(); slots[index] = { deps, cleanup: fn() };
      });
    },
  };
  const exports = {};
  const jsx = (type, props) => ({ type, props });
  new Function("require", "exports", "window", "document", code)(name => {
    if (name === "react") return react;
    if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
    if (name === "react-dom") return { createPortal: tree => tree };
    if (name.endsWith("projectRead")) return { withProjectReadTimeout };
    if (name.endsWith("useMotionPresence")) return { useMotionPresence: () => ({ mounted: true, phase: "entered" }) };
    if (name.endsWith("motion/tokens")) return { RETAIN: { panel: 420 } };
    if (name.endsWith("visiblePolling")) return { startVisiblePolling: read => { void read(); return () => {}; } };
    return {};
  }, exports, { loom: client, addEventListener() {}, removeEventListener() {}, setTimeout, clearTimeout }, { body: {} });
  return {
    click(title) {
      const find = node => {
        if (!node || typeof node !== "object") return;
        if (node.props?.onClick && (node.props.title === title || node.type === "button" && JSON.stringify(node.props.children)?.includes(title))) return node.props.onClick;
        for (const child of Object.values(node)) {
          const result = Array.isArray(child) ? child.map(find).find(Boolean) : find(child);
          if (result) return result;
        }
      };
      const handler = find(lastTree);
      assert.ok(handler, `Missing action: ${title}`);
      handler();
    },
    render(projectId) {
      cursor = 0;
      const tree = details === "memory"
        ? exports.ProjectMemoryCard({ projectId, open: true, running: false })
        : details === "files"
        ? exports.FilesWorkbench({ projectId, projectName: projectId, root: projectId, open: true, onClose() {} })
        : details
        ? exports.ProjectDetailsPanel({ project: { id: projectId, name: projectId, root: projectId }, threads: [], open: true, onClose() {}, onNewThread() {}, onOpenThread() {}, onSetInstructions() {} })
        : exports.ProjectRepositoryCard({ projectId, blocked: false, onChanged() {}, onNewThread() {}, onBusyChange() {} });
      effects.splice(0).forEach(fn => fn());
      lastTree = tree;
      return JSON.stringify(tree);
    },
  };
}

test("switching projects ignores the previous repository response", async () => {
  const replies = new Map();
  const harness = repositoryHarness({ call: (_, { projectId }) => new Promise(resolve => replies.set(projectId, resolve)) });
  harness.render("old");
  harness.render("current");
  replies.get("current")({ isRepo: true, branch: "current-branch", branches: [], worktrees: [] });
  await new Promise(resolve => setImmediate(resolve));
  replies.get("old")({ isRepo: true, branch: "old-branch", branches: [], worktrees: [] });
  await new Promise(resolve => setImmediate(resolve));
  const view = harness.render("current");
  assert.match(view, /current-branch/);
  assert.doesNotMatch(view, /old-branch/);
});

test("missing project bridge displays a recoverable error", () => {
  const harness = repositoryHarness(undefined);
  harness.render("project");
  assert.match(harness.render("project"), /本地主机连接不可用/);
});

test("stalled repository read stops loading and enables refresh", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const harness = repositoryHarness({ call: () => new Promise(() => {}) });
  harness.render("project");
  assert.match(harness.render("project"), /读取仓库/);
  t.mock.timers.tick(20_000);
  await new Promise(resolve => setImmediate(resolve));
  const view = harness.render("project");
  assert.match(view, /项目读取超时/);
  assert.doesNotMatch(view, /读取仓库/);
  assert.match(view, /"disabled":false/);
});

test("pending workspace never claims a clean or empty repository", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const harness = repositoryHarness({ call: () => new Promise(() => {}) }, true);
  harness.render("project");
  const view = harness.render("project");
  assert.match(view, /状态未知/);
  assert.match(view, /正在读取文件列表/);
  assert.doesNotMatch(view, /工作区暂无 Git 变更|没有检测到 Git 工作区变更|还没有可显示的文件|未接入|clean/);
});

test("workspace switching ignores late responses and shows the current project's files", async () => {
  const replies = new Map();
  const harness = repositoryHarness({ call: (_, { projectId }) => new Promise(resolve => replies.set(projectId, resolve)) }, true);
  harness.render("old");
  harness.render("current");
  const status = name => ({ git: { isRepo: true, branch: name, changedCount: 0, changedFiles: [] }, tree: { entries: [{ name, path: name, type: "file", depth: 0 }] } });
  replies.get("current")(status("current-file"));
  await new Promise(resolve => setImmediate(resolve));
  replies.get("old")(status("old-file"));
  await new Promise(resolve => setImmediate(resolve));
  const view = harness.render("current");
  assert.match(view, /current-file/);
  assert.doesNotMatch(view, /old-file/);
});

test("failed file listing reports failure rather than an empty directory", async () => {
  const harness = repositoryHarness({ call: () => Promise.reject(new Error("Host disconnected")) }, "files");
  harness.render("project");
  await new Promise(resolve => setImmediate(resolve));
  const view = harness.render("project");
  assert.match(view, /文件列表读取失败/);
  assert.match(view, /Host disconnected/);
  assert.doesNotMatch(view, /当前目录为空/);
});

test("an old project's completed staging cannot overwrite the new workspace", async () => {
  let finishStage;
  const status = name => ({ git: { isRepo: true, branch: name, changedCount: 1, changedFiles: [{ path: "a.txt", status: " M", index: " ", workingTree: "M" }] }, tree: { entries: [] } });
  const harness = repositoryHarness({ call(method, { projectId }) {
    return method === "project/git_stage"
      ? new Promise(resolve => { finishStage = resolve; })
      : Promise.resolve(status(projectId));
  } }, true);
  harness.render("old");
  await new Promise(resolve => setImmediate(resolve));
  harness.render("old");
  harness.click("暂存");
  harness.render("current");
  await new Promise(resolve => setImmediate(resolve));
  finishStage({ git: { ...status("old-stage-result").git } });
  await new Promise(resolve => setImmediate(resolve));
  const view = harness.render("current");
  assert.match(view, /current/);
  assert.doesNotMatch(view, /old-stage-result/);
});

test("archived memory view persists after rendering and loads archived rows", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = [];
  const harness = repositoryHarness({ call(method, params) {
    calls.push({ method, params });
    return Promise.resolve({ memories: [], memory: { total: 1, archived: 1 }, index: {} });
  } }, "memory");
  harness.render("project");
  await new Promise(resolve => setImmediate(resolve));
  harness.render("project");
  harness.click("已归档");
  harness.render("project");
  t.mock.timers.tick(220);
  await new Promise(resolve => setImmediate(resolve));
  const view = harness.render("project");
  assert.match(view, /筛选已归档记忆/);
  assert.ok(calls.some(call => call.method === "project/memory_list" && call.params.status === "archived"));
});
