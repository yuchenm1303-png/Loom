// Static scenes of the work log for visual review: node states.mjs <outDir> [theme=light] [dsf=2]
import fs from "node:fs";
import path from "node:path";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const [out, theme = "light", dsf = "2", only = ""] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const base = { threadId: "thread-1", turnId: "turn-1", status: "completed" };
const iso = (s) => new Date(Date.UTC(2026, 9, 10, 12, 0, s)).toISOString();
const user = { ...base, id: "user", type: "user_message", text: "帮我把登录页的邮箱校验补上", createdAt: iso(0) };
const message = (id, text, extra = {}) => ({ ...base, id, type: "assistant_message", phase: "commentary", text, ...extra });
let clock = 2;
const step = (seconds, extra) => { const start = clock; clock += seconds + 0.4; return { createdAt: iso(start), updatedAt: iso(start + seconds), ...extra }; };
const read = (id, file, seconds = 0.4, extra = {}) => ({ ...base, id, type: "tool_call", toolName: "read_workspace_text", arguments: { path: file }, content: "export function Login() {}", ...step(seconds, {}), ...extra });
const search = (id, q, seconds = 0.6, extra = {}) => ({ ...base, id, type: "tool_call", toolName: "search_workspace_text", arguments: { query: q }, content: "3 matches", ...step(seconds, {}), ...extra });
const exec = (id, cmd, seconds, extra = {}) => [
  { ...base, id: `${id}-exec`, type: "tool_call", toolName: "exec", arguments: { argv: cmd }, ...step(seconds, {}), ...extra.wrapper },
  { ...base, id, type: "process", argv: cmd, stdout: extra.stdout ?? "ok", ...extra.process },
];
const edit = (id, file, seconds = 0.8) => [
  { ...base, id: `${id}-patch`, type: "tool_call", toolName: "apply_patch", arguments: { patch: `*** Update File: ${file}` }, ...step(seconds, {}) },
  { ...base, id, type: "file_edit", paths: [file], diff: `diff --git a/${file} b/${file}\n--- a/${file}\n+++ b/${file}\n@@ -1,3 +1,7 @@\n export function required(v: string) {\n   return v.trim().length > 0;\n }\n+\n+export function isEmail(v: string): boolean {\n+  return /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(v.trim());\n+}\n-// old` },
];

const scenes = {
  mixed: () => { clock = 2; return { running: true, items: [user, message("intro", "先读登录表单和公共校验工具。"), read("a", "src/pages/Login.tsx"), read("b", "src/utils/validators.ts", 1.2), search("c", "isEmail", 0.7),
    ...edit("e", "src/utils/validators.ts", 2.1), ...exec("t", ["npm", "test", "--", "validators"], 14, { process: { status: "running", stdout: "RUN  v1.6.0\n ✓ src/utils/validators.test.ts (4)\n   ✓ rejects a missing @", createdAt: iso(20), updatedAt: iso(20) }, wrapper: { status: "running", createdAt: iso(20), updatedAt: iso(20) } })] }; },
  failed: () => { clock = 2; return { running: true, items: [user, message("intro", "先看一下环境。"), read("a", "src/pages/Login.tsx"), { ...read("b", "src/pages/Register.tsx"), status: "failed", content: "ENOENT: no such file or directory, open 'src/pages/Register.tsx'" },
    ...exec("t", ["sc", "stop", "wuauserv"], 0.7, { wrapper: { status: "failed", content: "Access is denied." }, process: { status: "failed", stdout: "", stderr: "Access is denied." } }),
    { ...base, id: "w", type: "tool_call", toolName: "browser_click", arguments: { name: "提交" }, status: "completed", result: { execution_status: "not_executed" }, ...step(0.2, {}) },
    { ...base, id: "ap", type: "tool_call", toolName: "exec", arguments: { argv: ["npm", "install"] }, status: "waiting_approval", ...step(0, {}) }] }; },
  long: () => { clock = 2; const rows = []; for (let i = 0; i < 11; i += 1) rows.push(read(`r${i}`, `src/components/Part${i}.tsx`, 0.3 + (i % 3) * 0.2)); rows.push({ ...search("s", "TODO"), status: "failed", content: "rg: not found" }); rows.push(read("last", "src/App.tsx", 0.5, { status: "running", createdAt: iso(40), updatedAt: iso(40) }));
    return { running: true, items: [user, message("intro", "把组件都看一遍。"), ...rows] }; },
  stages: () => { clock = 2; return { running: true, items: [user, message("intro", "先看一下。"), read("a", "src/a.ts"), read("b", "src/b.ts", 1.4), { ...search("c", "x"), status: "failed", content: "no result" }, message("r", "没搜到，换个关键词。", { stepId: "s1", text: "没搜到任何结果，换个关键词重新搜，同时把相关的配置文件也读一下，确认入口。" }), search("d", "isEmail"), read("e", "src/utils/validators.ts"), ...exec("t", ["npm", "run", "build"], 12, { process: { status: "running", stdout: "vite v7.1.0 building for production...\ntransforming (212) src/components/Transcript.tsx", createdAt: iso(30), updatedAt: iso(30) }, wrapper: { status: "running", createdAt: iso(30), updatedAt: iso(30) } })] }; },
  userreal: () => { clock = 2; return { running: true, items: [user, message("intro", "会话已释放（idle_expired），20c1b982… 不能续。先核对授权与 Bridge 状态再决定下一步。"),
    { ...base, id: "bs", type: "tool_call", toolName: "browser_status", arguments: {}, content: "ok", ...step(0.6, {}) },
    ...exec("ps", ["powershell", "-NoProfile", "-Command", "$p = Start-Process -FilePath 'C:\\Users\\demo\\AppData\\Local\\Programs\\Python\\Python312\\python.exe' -ArgumentList 'server.py' -PassThru -WindowStyle Hidden"], 1.4, { stdout: "ok" }),
    { ...base, id: "bo", type: "tool_call", toolName: "browser_open", arguments: { url: "http://127.0.0.1:8772/test_page.html" }, content: "ok", ...step(1.1, {}) },
    ...[{ ...base, id: "ev-patch", type: "tool_call", toolName: "apply_patch", arguments: { patch: "*** Update File: browser_acceptance_2026-10-09_v0125_current-browser/EVIDENCE_LOG.md" }, ...step(2.2, {}) },
      { ...base, id: "ev", type: "file_edit", paths: ["browser_acceptance_2026-10-09_v0125_current-browser/EVIDENCE_LOG.md"], diff: "diff --git a/x/EVIDENCE_LOG.md b/x/EVIDENCE_LOG.md\n--- a/x/EVIDENCE_LOG.md\n+++ b/x/EVIDENCE_LOG.md\n@@ -1,2 +1,2 @@\n+新\n-旧", ...step(0.2, {}) }],
    { ...base, id: "bc", type: "tool_call", toolName: "browser_click", arguments: {}, result: { execution_status: "not_executed" }, ...step(0.2, {}) },
    message("w", "授权瞬时 service_unavailable，我等 5s 后重试。".repeat(5), { stepId: "w" }),
    ...exec("sl", ["powershell", "-Command", "Start-Sleep -Seconds 30"], 30.2, { process: { status: "running", createdAt: new Date(Date.now() - 12000).toISOString(), updatedAt: new Date().toISOString() }, wrapper: { status: "running", createdAt: new Date(Date.now() - 12000).toISOString(), updatedAt: new Date().toISOString() } })] }; },
  hover: () => { clock = 2; return { running: true, items: [user, message("intro", "先看。"), read("a", "src/pages/Login.tsx"), ...edit("e", "src/utils/validators.ts", 2.1), ...exec("t", ["npm", "test"], 6.5, { stdout: "ok" }), message("r", "测试通过。这一句足够长，因此它是正文。".repeat(8), { stepId: "s" }), read("z", "src/z.ts", 1.2, { status: "running", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() })], hover: ".wv-step:nth-of-type(2) .wv-row" }; },
  open: () => { clock = 2; return { running: true, items: [user, message("intro", "先改校验。"), ...edit("e", "src/utils/validators.ts", 2.1), ...exec("t", ["npm", "test"], 6.5, { stdout: " PASS  src/utils/validators.test.ts\n  isEmail\n    ✓ accepts a plain address (2 ms)\n    ✓ rejects a missing @ (1 ms)\n\nTests: 6 passed, 6 total" }), read("z", "src/pages/Login.tsx")], open: [".wv-step:nth-of-type(1) .wv-row", ".wv-step:nth-of-type(2) .wv-row"] }; },
};

const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  for (const [name, build] of Object.entries(scenes)) {
    if (only && !only.split(",").includes(name)) continue;
    const page = await browser.newPage({ viewport: { width: 1000, height: 700 }, deviceScaleFactor: Number(dsf) });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=${theme}`);
    await page.waitForFunction(() => Boolean(window.motionFixture));
    const scene = build();
    await page.evaluate(([items, running]) => window.motionFixture.turn(items, running), [scene.items, scene.running]);
    await page.waitForTimeout(900);
    for (const selector of scene.open ?? []) { await page.locator(selector).first().click(); await page.waitForTimeout(500); }
    if (scene.hover) { await page.hover(scene.hover); await page.waitForTimeout(350); }
    if (scene.focus) { await page.focus(scene.focus); await page.keyboard.press("Shift+Tab"); await page.keyboard.press("Tab"); await page.waitForTimeout(250); }
    await page.waitForTimeout(500);
    const box = await page.locator(".turn-block").boundingBox();
    await page.screenshot({ path: path.join(out, `${name}-${theme}.png`), clip: { x: Math.max(0, box.x - 20), y: Math.max(0, box.y + 40), width: Math.min(820, 1000 - box.x), height: Math.min(560, box.height) } });
    if (errors.length) console.log(name, errors);
    await page.close();
  }
  console.log("done");
} finally { await browser.close(); }
