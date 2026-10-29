import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173"}/scripts/fixtures/liquid-cursor.html`);
  await page.waitForFunction(() => document.documentElement.classList.contains("loom-liquid-cursor-active"));
  await page.evaluate(() => {
    const root = document.querySelector("main");
    root.classList.remove("loom-site");
    const sheet = document.querySelector(".sheet"); sheet.replaceChildren();
    const tags = [
      '<button>Button</button>', '<a href="#noop" data-liquid-snap="false">Previously opted-out link</a>',
      '<input type="text" value="Text input">', '<input type="checkbox">',
      '<textarea>Textarea</textarea>', '<select><option>Select</option></select>',
      '<details><summary>Details summary</summary>Content</details>',
      '<label for="label-control">Checkbox label</label><input id="label-control" type="checkbox">',
      '<div role="button" tabindex="0">Custom button</div>', '<div role="tab">Tab</div>',
      '<div role="menuitem">Menu item</div>', '<div tabindex="0">Focusable control</div>',
      '<div onclick="this.dataset.clicks=String(Number(this.dataset.clicks||0)+1)">Click handler</div>',
      '<button disabled>Disabled</button>', '<div role="button" aria-disabled="true">Unavailable</div>',
    ];
    for (const [index, markup] of tags.entries()) {
      const host = document.createElement("section"); host.innerHTML = markup;
      host.style.cssText = `position:fixed;left:${70 + (index % 3) * 370}px;top:${80 + Math.floor(index / 3) * 130}px`;
      const element = host.firstElementChild;
      const target = element.matches("details") ? element.querySelector("summary") : element;
      target.id = `control-${index}`;
      target.style.cssText = "display:block;box-sizing:border-box;width:220px;height:42px;background:#ddd;color:#111;padding:8px";
      sheet.append(host);
    }
  });
  for (let index = 0; index < 15; index++) {
    const selector = `#control-${index}`;
    await page.locator(selector).hover();
    await page.waitForTimeout(1300);
    const state = await page.evaluate(() => {
      const canvas = document.querySelector(".loom-liquid-cursor-canvas");
      const gl = canvas.getContext("webgl"); const program = gl.getParameter(gl.CURRENT_PROGRAM);
      const size = gl.getUniform(program, gl.getUniformLocation(program, "u_lensSizes[0]"));
      const settings = gl.getUniform(program, gl.getUniformLocation(program, "u_lensSettings[0]"));
      return { width: size[0], height: size[1], strength: settings[1] };
    });
    assert.ok(Math.abs(state.width - (index < 13 ? 240 : 80)) < 1, `${selector}: width ${state.width}`);
    assert.ok(Math.abs(state.strength - .95) < .0001);
  }
  await page.locator("#control-6").click();
  assert.equal(await page.locator("details").getAttribute("open"), "");
  await page.locator("#control-12").click();
  assert.equal(await page.locator("#control-12").getAttribute("data-clicks"), "1");
  assert.deepEqual(errors, []);
  console.log("PASS: unmarked buttons/links/inputs/checkboxes/textarea/select/summary/labels/roles/tabindex/click handlers mount; disabled controls do not; clicks remain native");
} finally { await browser.close(); }
