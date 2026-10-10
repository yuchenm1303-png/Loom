// Detect stacked motion during a live run: nested elements fading at the same moment (their opacities multiply),
// and elements with two animations or transitions on one property.
//   node conflicts.mjs <url> [durationMs=24000]
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const [url, duration = "24000"] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  page.on("pageerror", (error) => console.log("pageerror", error.message));
  await page.addInitScript(() => {
    const describe = (el) => {
      const cls = [...el.classList].slice(0, 2).join(".");
      return `${el.tagName.toLowerCase()}${cls ? "." + cls : ""}`;
    };
    const pairs = new Map();
    const doubles = new Map();
    const props = (animation) => {
      if (animation.transitionProperty) return [animation.transitionProperty];
      try { return [...new Set(animation.effect.getKeyframes().flatMap((k) => Object.keys(k)))].filter((k) => !["offset", "easing", "composite", "computedOffset"].includes(k)); } catch { return []; }
    };
    window.__conflicts = { pairs, doubles };
    const sample = () => {
      const running = document.getAnimations().filter((a) => a.playState === "running" && a.effect?.target && a.effect.getComputedTiming().progress < 0.98);
      const byTarget = new Map();
      for (const animation of running) {
        const target = animation.effect.target;
        if (!byTarget.has(target)) byTarget.set(target, []);
        byTarget.get(target).push(animation);
      }
      const fading = [...byTarget].filter(([, list]) => list.some((a) => props(a).includes("opacity"))).map(([el]) => el);
      for (let i = 0; i < fading.length; i += 1) {
        for (let j = 0; j < fading.length; j += 1) {
          if (i !== j && fading[i].contains(fading[j])) {
            const key = `${describe(fading[i])}  >  ${describe(fading[j])}`;
            pairs.set(key, (pairs.get(key) || 0) + 1);
          }
        }
      }
      for (const [el, list] of byTarget) {
        const seen = new Map();
        for (const animation of list) for (const property of props(animation)) {
          if (animation.iterations === Infinity) continue;
          seen.set(property, (seen.get(property) || 0) + 1);
        }
        for (const [property, count] of seen) if (count > 1) { const key = `${describe(el)} :: ${property} x${count}`; doubles.set(key, (doubles.get(key) || 0) + 1); }
      }
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  await page.goto(url);
  if (/live-app/.test(url)) {
    await page.waitForFunction(() => window.liveFixture && document.querySelector(".composer textarea:not([disabled])"));
    await page.waitForTimeout(800);
    await page.evaluate(() => window.liveFixture.send());
  }
  await page.waitForTimeout(Number(duration));
  const out = await page.evaluate(() => ({ pairs: [...window.__conflicts.pairs], doubles: [...window.__conflicts.doubles] }));
  console.log("nested elements fading together (parent > child : frames):");
  for (const [key, count] of out.pairs.sort((a, b) => b[1] - a[1]).slice(0, 25)) console.log(`${String(count).padStart(5)}  ${key}`);
  console.log("\nsame property animated twice on one element:");
  for (const [key, count] of out.doubles.sort((a, b) => b[1] - a[1]).slice(0, 25)) console.log(`${String(count).padStart(5)}  ${key}`);
} finally { await browser.close(); }
