// Frame-by-frame probes of single transitions in the work log.
//   node probe.mjs [theme=light] [only]
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const [theme = "light", only = ""] = process.argv.slice(2);
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5199";
const base = { threadId: "thread-1", turnId: "turn-1", status: "completed" };
const iso = (s) => new Date(Date.UTC(2026, 9, 10, 12, 0, s)).toISOString();
const user = { ...base, id: "user", type: "user_message", text: "检查", createdAt: iso(0) };
const message = (id, text, extra = {}) => ({ ...base, id, type: "assistant_message", phase: "commentary", text, ...extra });
const read = (id, seconds = 0.4, extra = {}) => ({ ...base, id, type: "tool_call", toolName: "read_workspace_text", arguments: { path: `src/${id}.ts` }, content: "ok", createdAt: iso(2), updatedAt: iso(2 + seconds), ...extra });
const running = (id) => read(id, 0, { status: "running", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(), content: undefined });
const cmd = (id, status = "completed", extra = {}) => [
  { ...base, id: `${id}-x`, type: "tool_call", toolName: "exec", arguments: { argv: ["npm", "test"] }, status, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() },
  { ...base, id, type: "process", argv: ["npm", "test"], status, stdout: extra.stdout ?? "", stderr: extra.stderr ?? "", createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() },
];

const cases = {
  append: { title: "a step is appended to a live stage", before: [user, message("intro", "先看。"), read("a"), read("b")], after: [user, message("intro", "先看。"), read("a"), read("b"), running("c")], watch: [".wv-stage", ".turn-process-grid"], ms: 700 },
  complete: { title: "a running step completes", before: [user, message("intro", "先看。"), read("a"), running("b")], after: [user, message("intro", "先看。"), read("a"), read("b")], watch: [".wv-stage", ".wv-step:last-child .wv-glyph"], ms: 900 },
  tail: { title: "a command prints, then finishes", before: [user, message("intro", "先看。"), ...cmd("t", "running", { stdout: "RUN v1" })], after: [user, message("intro", "先看。"), ...cmd("t", "completed", { stdout: "RUN v1\nPASS" })], watch: [".wv-stage", ".wv-sub-fold"], ms: 900 },
  fail: { title: "a step fails", before: [user, message("intro", "先看。"), read("a"), running("b")], after: [user, message("intro", "先看。"), read("a"), read("b", 1, { status: "failed", content: "ENOENT: no such file" })], watch: [".wv-stage", ".wv-sub-fold"], ms: 900 },
  supersede: { title: "the stage is over: the model speaks and a new stage starts", before: [user, message("intro", "先看。"), read("a"), read("b"), read("c", 0.5, { status: "failed", content: "nope" })], after: [user, message("intro", "先看。"), read("a"), read("b"), read("c", 0.5, { status: "failed", content: "nope" }), message("r", "没读到，换条路。这是一句足够长的回复，因为失败之后模型的反应应该作为正文留下来。", { stepId: "s", text: "没读到，换条路。".repeat(14) }), running("d")], watch: [".wv-stage", ".turn-process-grid", ".wv-summary-fold", ".wv-body-slot > .wv-fold"], ms: 900 },
  window: { title: "the seventh and eighth steps arrive in a live stage", before: [user, message("intro", "看。"), ...["a", "b", "c", "d", "e", "f"].map((id) => read(id))], after: [user, message("intro", "看。"), ...["a", "b", "c", "d", "e", "f", "g"].map((id) => read(id)), running("h")], watch: [".wv-stage", ".turn-process-grid"], ms: 900 },
};

const story = [user, message("a1", "先看一下环境。"), read("t1", 0.4, { status: "failed", content: "no" }), message("a2", "引号被吞了，改用单引号，这是一句足够长的话。".repeat(6)), read("t2"), read("t3"), read("t4")];
const answer = message("final", "环境探完了。\n\n- 一\n- 二\n- 三", { phase: "final_answer" });
cases.complete_turn = { title: "the turn completes: hold, then fold", live: true, before: story, after: [...story, answer], afterRunning: false, watch: [".turn-process-grid", ".turn-process-header-shell", ".turn-final-answer", ".turn-final-answer .message-meta"], ms: 1500 };
cases.complete_scroll = { title: "completion keeps the viewport", live: true, before: story, after: [...story, answer], afterRunning: false, watch: [".turn-process-grid", ".turn-final-answer"], scroll: true, ms: 1500 };
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  for (const [name, spec] of Object.entries(cases)) {
    if (only && !only.split(",").includes(name)) continue;
    const page = await browser.newPage({ viewport: { width: 1100, height: 800 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${origin}/scripts/fixtures/runtime-motion.html?theme=${theme}`);
    await page.waitForFunction(() => Boolean(window.motionFixture));
    await page.evaluate(([items]) => window.motionFixture.turn(items, true), [spec.before]);
    await page.waitForTimeout(1200);
    const frames = await page.evaluate(async ([items, watch, ms, running]) => {
      const out = [];
      const start = performance.now();
      window.motionFixture.turn(items, running);
      while (performance.now() - start < ms) {
        out.push({ t: Math.round(performance.now() - start), v: watch.map((selector) => {
          const el = document.querySelector(selector);
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return [Math.round(r.height * 10) / 10, Math.round(Number(getComputedStyle(el).opacity) * 100) / 100, Math.round(r.top * 10) / 10];
        }) });
        await new Promise(requestAnimationFrame);
      }
      return out;
    }, [spec.after, spec.watch, spec.ms, spec.afterRunning ?? true]);
    console.log(`\n== ${name}: ${spec.title}`);
    console.log("   t(ms)  " + spec.watch.map((s) => s.slice(-22).padEnd(24)).join(""));
    let last = "";
    frames.forEach((frame, index) => {
      const row = frame.v.map((v) => (v ? `h${String(v[0]).padStart(6)} o${v[1].toFixed(2)} y${String(v[2]).padStart(6)}` : "      —              ").padEnd(24)).join("");
      if (row !== last || index === frames.length - 1) console.log(`${String(frame.t).padStart(6)}  ${row}`);
      last = row;
    });
    if (errors.length) console.log("ERRORS", errors);
    await page.close();
  }
} finally { await browser.close(); }
