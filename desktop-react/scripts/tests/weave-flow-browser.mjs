import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const base = { threadId: "thread-1", turnId: "turn-1", status: "completed" };
const iso = seconds => new Date(Date.UTC(2026, 9, 10, 12, 0, 0) + seconds * 1000).toISOString();
const user = { ...base, id: "user", type: "user_message", text: "检查任务", createdAt: iso(0) };
const message = (id, text, extra = {}) => ({ ...base, id, type: "assistant_message", phase: "commentary", text, ...extra });
const read = (id, path, extra = {}) => ({ ...base, id, type: "tool_call", toolName: "read_workspace_text", arguments: { path }, content: "ok", createdAt: iso(1), updatedAt: iso(1.4), ...extra });
const command = (id, argv, extra = {}) => [
  { ...base, id: `${id}-exec`, type: "tool_call", toolName: "exec", arguments: { argv }, createdAt: iso(2), updatedAt: iso(4.4), ...extra.wrapper },
  { ...base, id, type: "process", argv, stdout: "", stderr: "", createdAt: iso(2.03), updatedAt: iso(4.4), ...extra.process },
];

try {
  for (const theme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 1200, height: 1000 } });
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=${theme}`);
    await page.waitForFunction(() => Boolean(window.motionFixture));
    const render = (items, live = true) => page.evaluate(([items, live]) => window.motionFixture.turn(items, live), [items, live]);
    const intro = message("intro", "先看一下。");

    // A step's words never change while it runs and when it ends: its state is the node, so nothing reflows.
    const runningRead = read("a", "src/pages/Login.tsx", { status: "running", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), content: undefined });
    await render([user, intro, runningRead]);
    await page.locator('.wv-step[data-tone="running"]').waitFor();
    const before = await page.evaluate(() => ({ verb: document.querySelector(".wv-verb").textContent, target: document.querySelector(".wv-target").textContent }));
    const step = await page.locator(".wv-step").elementHandle();
    const verb = await page.locator(".wv-verb").elementHandle();
    await render([user, intro, { ...runningRead, status: "completed", content: "ok", updatedAt: new Date().toISOString() }]);
    await page.locator('.wv-step[data-tone="done"]').waitFor();
    const after = await page.evaluate(() => ({ verb: document.querySelector(".wv-verb").textContent, target: document.querySelector(".wv-target").textContent }));
    assert.deepEqual(after, before, "the words of a row are the same running and done");
    assert.equal(before.verb, "读取", "the verb has no tense");
    assert.equal(await step.evaluate(node => node === document.querySelector(".wv-step")), true, "the same row, not a new one");
    assert.equal(await verb.evaluate(node => node === document.querySelector(".wv-verb")), true, "the verb was never remounted");

    // A path is its folder (quiet) and its file name (the thing).
    assert.equal(await page.locator(".wv-target .wv-dir").innerText(), "src/pages/");
    assert.equal(await page.locator(".wv-target .wv-base").innerText(), "Login.tsx");

    // How long a step took, once it took a second or more; a faster one says nothing.
    await render([user, intro, read("slow", "slow.ts", { updatedAt: iso(3.4) }), read("quick", "quick.ts", { createdAt: iso(5), updatedAt: iso(5.2) })]);
    await page.waitForFunction(() => document.querySelectorAll(".wv-step").length === 2);
    assert.deepEqual(await page.locator(".wv-step").evaluateAll(steps => steps.map(step => step.querySelector(".wv-time")?.textContent ?? null)), ["2.4s", null]);
    // A running step counts up on its own.
    const started = new Date(Date.now() - 2500).toISOString();
    await render([user, intro, read("long", "long.ts", { status: "running", createdAt: started, updatedAt: started, content: undefined })]);
    await page.locator(".wv-step .wv-time").waitFor();
    const first = await page.locator(".wv-step .wv-time").innerText();
    await page.waitForFunction(text => document.querySelector(".wv-step .wv-time")?.textContent !== text, first, { timeout: 3000 });

    // A running command shows the last thing it printed under its row, and that line folds away when it ends.
    const running = { wrapper: { status: "running" }, process: { status: "running", stdout: "RUN v1.6\n\u001b[32m✓\u001b[0m rejects a missing @\n" } };
    await render([user, intro, ...command("t", ["npm", "test"], running)]);
    await page.locator(".wv-sub.is-tail").waitFor();
    assert.equal(await page.locator(".wv-sub.is-tail").innerText(), "✓ rejects a missing @", "the last line, without terminal codes");
    await render([user, intro, ...command("t", ["npm", "test"], { process: { stdout: "all passed" } })]);
    await page.waitForFunction(() => !document.querySelector(".wv-sub"), null, { timeout: 2000 });

    // Why a step failed is on its row, with the outcome in words as well as colour.
    await render([user, intro, read("bad", "src/pages/Register.tsx", { status: "failed", content: "ENOENT: no such file or directory\nat open (fs.js:1)" })]);
    await page.locator(".wv-sub.is-failure").waitFor();
    assert.equal(await page.locator(".wv-sub.is-failure").innerText(), "ENOENT: no such file or directory");
    assert.equal(await page.locator(".wv-tag").innerText(), "失败");
    assert.equal(await page.locator('.wv-step[data-tone="failed"] .wv-node svg').count(), 1, "its node is its own mark, not the kind of step");
    const colours = await page.evaluate(() => {
      const verbColour = getComputedStyle(document.querySelector(".wv-verb")).color;
      const targetColour = getComputedStyle(document.querySelector(".wv-target")).color;
      return { verbColour, targetColour };
    });
    assert.notEqual(colours.verbColour, colours.targetColour, "a failed step reads differently from the path it names");

    // Not executed, and waiting for approval, are said in words too.
    await render([user, intro,
      { ...base, id: "click", type: "tool_call", toolName: "browser_click", arguments: { name: "提交" }, result: { execution_status: "not_executed" }, createdAt: iso(1), updatedAt: iso(1.1) },
      { ...base, id: "ask", type: "tool_call", toolName: "exec", arguments: { argv: ["npm", "install"] }, status: "waiting_approval", createdAt: iso(2), updatedAt: iso(2) }]);
    await page.waitForFunction(() => document.querySelectorAll(".wv-tag").length === 2);
    assert.deepEqual(await page.locator(".wv-tag").allInnerTexts(), ["未执行", "等待确认"]);
    assert.deepEqual(await page.locator(".wv-step").evaluateAll(steps => steps.map(step => step.dataset.tone)), ["stopped", "waiting"]);

    // A row opens into the full command and its output; an edit opens into a diff with its changes marked.
    const diff = "diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ b/a.ts\n@@ -1,2 +1,2 @@\n context\n-old line\n+new line";
    await render([user, intro, ...command("t", ["npm", "test", "--", "validators"], { process: { stdout: "PASS ok\n6 passed" } }),
      { ...base, id: "p-patch", type: "tool_call", toolName: "apply_patch", arguments: { patch: "*** Update File: a.ts" }, createdAt: iso(5), updatedAt: iso(5.5) },
      { ...base, id: "edit", type: "file_edit", paths: ["a.ts"], diff, createdAt: iso(5.6), updatedAt: iso(5.6) }]);
    await page.waitForFunction(() => document.querySelectorAll(".wv-step").length === 2);
    await page.locator(".wv-step").first().locator(".wv-row").click();
    await page.locator(".wv-step").first().locator('.wv-detail[data-open="true"]').waitFor();
    assert.equal(await page.locator(".wv-step").first().locator(".wv-sheet-command code").innerText(), "npm test -- validators");
    assert.match(await page.locator(".wv-step").first().locator(".wv-sheet-output").innerText(), /6 passed/);
    await page.locator(".wv-step").nth(1).locator(".wv-row").click();
    await page.locator(".wv-step").nth(1).locator('.wv-detail[data-open="true"]').waitFor();
    assert.equal(await page.locator(".wv-diff-line.is-add").innerText(), "+new line");
    assert.equal(await page.locator(".wv-diff-line.is-del").innerText(), "-old line");
    assert.equal(await page.locator(".wv-diff-line.is-hunk").count(), 1);

    // A step appended to a live stage grows into its place: the stage's height never jumps, frame by frame.
    await page.evaluate(() => window.motionFixture.reset());
    const stageItems = [user, intro, read("a", "a.ts"), read("b", "b.ts")];
    await render(stageItems);
    await page.waitForTimeout(500);
    const grow = await page.evaluate(async items => {
      const samples = [];
      window.motionFixture.turn(items, true);
      const start = performance.now();
      while (performance.now() - start < 600) {
        samples.push(document.querySelector(".wv-stage").getBoundingClientRect().height);
        await new Promise(requestAnimationFrame);
      }
      return samples;
    }, [...stageItems, read("c", "c.ts", { status: "running", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), content: undefined })]);
    const jumps = grow.slice(1).map((height, index) => height - grow[index]);
    assert.ok(grow.at(-1) - grow[0] >= 27, `the stage gains a row: ${grow[0]} -> ${grow.at(-1)}`);
    assert.ok(Math.max(...jumps) < 13 && Math.min(...jumps) > -1, `no frame jumps a row at once: ${jumps.map(value => value.toFixed(1)).join(" ")}`);
    assert.ok(grow.some(height => height > grow[0] + 3 && height < grow.at(-1) - 3), "it passes through intermediate heights");

    // The same holds when the stage is long: a step arrives while an older one folds into the line.
    const eight = ["a", "b", "c", "d", "e", "f", "g", "h"].map(id => read(id, `${id}.ts`));
    await render([user, intro, ...eight.slice(0, 6)]);
    await page.waitForTimeout(500);
    const retire = await page.evaluate(async items => {
      const samples = [];
      window.motionFixture.turn(items, true);
      const start = performance.now();
      while (performance.now() - start < 700) {
        samples.push(document.querySelector(".wv-stage").getBoundingClientRect().height);
        await new Promise(requestAnimationFrame);
      }
      return samples;
    }, [user, intro, ...eight]);
    const steps = retire.slice(1).map((height, index) => Math.abs(height - retire[index]));
    assert.ok(Math.max(...steps) < 16, `steps arriving while older ones fold move smoothly: ${steps.map(value => value.toFixed(1)).join(" ")}`);

    // The switch for the model's notes appears the first time a note is held back. It grows into its place like a step,
    // instead of shoving the whole turn down by a line in one frame.
    await page.evaluate(() => window.motionFixture.reset());
    const quietUser = { ...user, id: "user-notes" };
    await render([quietUser, intro, read("a", "a.ts"), read("b", "b.ts")]);
    await page.waitForTimeout(400);
    assert.equal(await page.locator(".process-notes-row").count(), 0, "nothing was held back, so there is no switch");
    const shove = await page.evaluate(async items => {
      const samples = [];
      window.motionFixture.turn(items, true);
      const start = performance.now();
      while (performance.now() - start < 600) {
        samples.push(document.querySelector('[data-message-id="intro"]').getBoundingClientRect().top);
        await new Promise(requestAnimationFrame);
      }
      return samples;
    }, [quietUser, intro, read("a", "a.ts"), message("quiet", "继续。"), read("b", "b.ts")]);
    assert.equal(await page.locator(".process-notes-row").count(), 1, "a note was held back: the switch appears");
    const shoves = shove.slice(1).map((top, index) => top - shove[index]);
    assert.ok(shove.at(-1) - shove[0] >= 15, `the turn makes room for it: ${shove[0]} -> ${shove.at(-1)}`);
    assert.ok(Math.max(...shoves) < 10, `in more than one frame: ${shoves.map(value => value.toFixed(1)).join(" ")}`);
    assert.ok(shove.some(top => top > shove[0] + 3 && top < shove.at(-1) - 3), "through intermediate positions");

    // Reduced motion keeps every state and plays nothing.
    await page.evaluate(() => { window.motionFixture.reset(); document.documentElement.dataset.loomReducedMotion = "true"; });
    await render([user, intro, read("a", "a.ts", { status: "running", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), content: undefined })]);
    await page.locator('.wv-step[data-tone="running"]').waitFor();
    await page.waitForTimeout(150);
    const moving = await page.evaluate(() => document.getAnimations().filter(animation => animation.effect?.target?.closest?.(".wv-stage") && animation.playState === "running").length);
    assert.equal(moving, 0, "no animation is running in the log under reduced motion");
    await page.evaluate(() => { document.documentElement.dataset.loomReducedMotion = "false"; });

    assert.deepEqual(errors, []);
    await page.close();
  }

  // English: the same verbs, bare, with the preposition they need.
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  await page.addInitScript(() => localStorage.setItem("loom.settings.language", "en"));
  await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=light`);
  await page.waitForFunction(() => Boolean(window.motionFixture));
  await page.evaluate(items => window.motionFixture.turn(items, true), [user, message("intro", "Let me look."),
    read("a", "src/App.tsx"), { ...base, id: "s", type: "tool_call", toolName: "search_workspace_text", arguments: { query: "isEmail" }, createdAt: iso(2), updatedAt: iso(2.2) },
    { ...base, id: "w", type: "tool_call", toolName: "browser_wait", arguments: {}, createdAt: iso(3), updatedAt: iso(3.2) }]);
  await page.waitForFunction(() => document.querySelectorAll(".wv-step").length === 3);
  assert.deepEqual(await page.locator(".wv-verb").allInnerTexts(), ["Read", "Search", "Wait for"]);
  await page.close();
  console.log("Weave log: stable verbs, node states, paths, durations, running output, failure reasons, tags, drawers, smooth growth and folding, reduced motion and English passed in both themes.");
} finally { await browser.close(); }
