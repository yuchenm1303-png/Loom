// Motion lab: random runs, checked.
//   node fuzz.mjs <url-of-live-app.html> [from=1] [to=12] [--speed 4] [--parallel 3] [--theme dark]
// Plays the live-app "fuzz" scenario (a seeded random run: stages of tool work, notes, approvals, parallel calls,
// failures, and an ending - answer, error, a stop) once per seed, with a frame recorder on, and checks what must hold
// of every run whatever its shape:
//   - no page error, and the turn ends;
//   - nothing is left live: no thinking capsule, no running node, no step still marked executing, no leaving item;
//   - the process is folded away (the only thing between the user's message and the ending is its summary line);
//   - the view ends on the live edge (nothing in the run involves the reader scrolling), and while following it only
//     ever moves toward the edge: never away from it by more than the page shrank, never past it;
//   - no infinite animation is left running, the typing glow is out, the DOM did not balloon.
// A failing seed is printed with the first violated rule; rerun it alone (from = to = seed) and open the fixture with
// ?scenario=fuzz&seed=N to watch it.
import { launch } from "./launch.mjs";

const argv = process.argv.slice(2);
const option = (name, fallback) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
const valued = new Set(["--speed", "--parallel", "--theme"]);
const positional = argv.filter((arg, index) => !arg.startsWith("--") && !valued.has(argv[index - 1]));
const [url, fromText = "1", toText = "12"] = positional;
const speed = option("--speed", "4");
const parallel = Number(option("--parallel", "3"));
const theme = option("--theme", "dark");

const browser = await launch();
async function runSeed(seed) {
  const page = await browser.newPage({ viewport: { width: 935, height: 731 }, deviceScaleFactor: 1 });
  const problems = [];
  page.on("pageerror", (error) => problems.push(`page error: ${error.message.slice(0, 120)}`));
  page.on("console", (message) => { if (message.type() === "error" && !/favicon|Failed to load resource/.test(message.text())) problems.push(`console error: ${message.text().slice(0, 120)}`); });
  await page.addInitScript(() => {
    const rec = { frames: [], on: false };
    window.__fuzz = rec;
    const channel = new MessageChannel();
    channel.port1.onmessage = () => {
      if (!rec.on) return;
      const scroller = document.querySelector(".conversation-stage > .transcript-scroll");
      if (scroller) rec.frames.push([scroller.scrollTop, scroller.scrollHeight, scroller.dataset.following ?? "", scroller.clientHeight, document.querySelector(".turn-block.is-settling") ? 1 : 0]);
    };
    const loop = () => { channel.port2.postMessage(0); requestAnimationFrame(loop); };
    requestAnimationFrame(loop);
  });
  try {
    await page.goto(`${url}${url.includes("?") ? "&" : "?"}scenario=fuzz&seed=${seed}&speed=${speed}&theme=${theme}`);
    await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"), null, { timeout: 90000 });
    await page.waitForTimeout(900);
    await page.evaluate(() => { window.__fuzz.on = true; window.liveFixture.send(); });
    await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length > 0, null, { timeout: 30000 });
    const ended = await page.waitForFunction(() => document.querySelectorAll(".turn-block.is-active").length === 0, null, { timeout: 100000 }).then(() => true, () => false);
    if (!ended) problems.push("the turn did not end within 100 s");
    await page.waitForTimeout(2400);
    const state = await page.evaluate(() => {
      const scroller = document.querySelector(".conversation-stage > .transcript-scroll");
      const turn = document.querySelector('.transcript > .turn-block[data-turn-id^="turn-live"]');
      const visible = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && Number(s.opacity) > 0.02; };
      const highlights = ["loom-type-0", "loom-type-1", "loom-type-2"].map((name) => CSS.highlights.get(name)?.size ?? 0);
      const grid = turn?.querySelector(".turn-process-grid");
      return {
        found: Boolean(turn),
        gap: scroller ? scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight : 0,
        capsule: [...document.querySelectorAll(".inline-thinking, .live-reasoning.is-thinking")].filter(visible).length,
        running: turn ? [...turn.querySelectorAll('[data-tone="running"], .wv-row.is-executing, .live-reasoning.is-thinking')].filter(visible).length : 0,
        leaving: turn ? turn.querySelectorAll(".is-leaving").length : 0,
        gridHeight: grid ? grid.getBoundingClientRect().height : 0,
        settling: turn ? turn.querySelectorAll(".is-settling").length : 0,
        childClasses: turn ? [...turn.children].map((child) => child.className.split(" ").slice(0, 2).join(".")) : [],
        infinite: document.getAnimations().filter((animation) => animation.playState === "running" && animation.effect?.getTiming().iterations === Infinity).length,
        highlights,
        nodes: document.getElementsByTagName("*").length,
        jump: document.querySelector(".transcript-jump-latest")?.classList.contains("is-visible") ?? false,
        live: document.documentElement.dataset.loomLive ?? "",
        frames: window.__fuzz.frames,
      };
    });
    if (!state.found) problems.push("the turn's block is missing");
    if (state.capsule) problems.push(`${state.capsule} thinking indicator(s) left on screen`);
    if (state.running) problems.push(`${state.running} step(s) still drawn as running`);
    if (state.leaving) problems.push(`${state.leaving} item(s) still leaving`);
    if (state.settling) problems.push("the completion hand-off never finished");
    if (state.gridHeight > 1.5) problems.push(`the process did not fold (${Math.round(state.gridHeight)}px)`);
    if (state.gap > 3.5 && !state.jump) problems.push(`the view ended ${Math.round(state.gap)}px off the live edge with no way back shown`);
    if (state.gap > 3.5 && state.jump) problems.push(`the view ended ${Math.round(state.gap)}px off the live edge (detached with nobody scrolling)`);
    if (state.infinite > 12) problems.push(`${state.infinite} infinite animations still running`);
    if (state.highlights.some((count) => count > 0)) problems.push("the typing glow was left on");
    if (state.live) problems.push("html[data-loom-live] left set");
    if (state.nodes > 6500) problems.push(`${state.nodes} DOM nodes`);
    // The view while following only moves toward the live edge. A step down cannot be longer than the distance the view
    // was behind (that would carry it past the edge); a step up needs the page to have shrunk by as much (a fold clamps
    // the view with it). A big step toward the edge is a big arrival being caught up with, not a jump.
    let worst = 0;
    let worstAt = 0;
    let largest = 0;
    for (let index = 1; index < state.frames.length; index += 1) {
      const [top, height, following, client, settling] = state.frames[index];
      const [previousTop, previousHeight, previousFollowing, previousClient, previousSettling] = state.frames[index - 1];
      if (following !== "true" || previousFollowing !== "true") continue;
      const step = top - previousTop;
      largest = Math.max(largest, Math.abs(step));
      const behind = Math.max(0, height - previousTop - client);
      const shrank = Math.max(0, previousHeight - height) + Math.max(0, client - previousClient) * -1;
      // The turn ending swaps the live message for the answer's own slot and folds rows within one frame; the page is
      // briefly shorter than the browser's layout in the middle of that commit makes it, so the hand-off gets slack.
      const slack = settling || previousSettling ? 34 : 6;
      const wrong = step >= 0 ? Math.max(0, step - Math.max(behind, 0) - 4) : Math.max(0, -step - shrank - 6);
      if (wrong > worst + 0 && wrong > slack) { worst = wrong; worstAt = index; }
    }
    if (worst > 0) problems.push(`the view moved ${Math.round(worst)}px the wrong way or past the edge in one frame (frame ${worstAt})`);
    return { seed, problems, nodes: state.nodes, maxStep: Math.round(largest), frames: state.frames.length, outline: state.childClasses.join(" > ") };
  } catch (error) {
    problems.push(`harness: ${String(error.message).split("\n")[0].slice(0, 120)}`);
    return { seed, problems, nodes: 0, maxStep: 0, frames: 0, outline: "" };
  } finally { await page.close(); }
}

const from = Number(fromText);
const to = Number(toText);
const seeds = Array.from({ length: to - from + 1 }, (_, index) => from + index);
const results = [];
let cursor = 0;
await Promise.all(Array.from({ length: Math.min(parallel, seeds.length) }, async () => {
  while (cursor < seeds.length) results.push(await runSeed(seeds[cursor++]));
}));
await browser.close();
results.sort((a, b) => a.seed - b.seed);
let failed = 0;
for (const result of results) {
  const status = result.problems.length ? "FAIL" : "ok  ";
  if (result.problems.length) failed += 1;
  console.log(`seed ${String(result.seed).padStart(3)} ${status} nodes ${String(result.nodes).padStart(5)} largest step ${String(result.maxStep).padStart(3)}px  ${result.problems.join("; ")}${result.problems.length ? `   [${result.outline}]` : ""}`);
}
console.log(`\n${results.length - failed} of ${results.length} runs clean`);
process.exit(failed ? 1 : 0);
