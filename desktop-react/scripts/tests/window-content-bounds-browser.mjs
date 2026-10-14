import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const css = ["components/artifact-preview-dock.css", "components/review-workspace.css", "components/review-dock.css", "components/sub-agent-dock.css", "components/profile-insights.css", "components/window-chrome.css"]
  .map(file => readFileSync(new URL(`../../src/${file}`, import.meta.url), "utf8")).join("\n");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
try {
  for (const height of [560, 900]) {
    for (const zoom of [1, 1.25]) {
      const page = await browser.newPage({ viewport: { width: 1280, height } });
      await page.setContent(`<style>html,body{height:100%;margin:0} ${css}</style>
        <div class="loom-window-shell"><header class="loom-titlebar"><div class="loom-titlebar-drag"></div><div class="loom-window-controls"><button>−</button><button>□</button><button>×</button></div></header><div class="loom-window-content"></div></div>`);
      await page.evaluate(zoom => {
        document.documentElement.classList.add("loom-desktop-chrome");
        document.documentElement.style.zoom = String(zoom);
      }, zoom);
      // The window controls float over the app, so the app and the root-portaled work surfaces
      // share the whole window: nothing is reserved above them and nothing runs past the bottom.
      const shell = await page.evaluate(() => {
        const bar = document.querySelector(".loom-titlebar").getBoundingClientRect();
        const content = document.querySelector(".loom-window-content").getBoundingClientRect();
        return { barTop: bar.top, barHeight: bar.height, contentTop: content.top, contentHeight: content.height };
      });
      assert.equal(shell.contentTop, 0, "the app starts at the top of the window");
      assert.ok(Math.abs(shell.contentHeight - height) <= 1, `the app fills the window: ${JSON.stringify(shell)}`);
      assert.equal(shell.barTop, 0);
      assert.ok(Math.abs(shell.barHeight - 60 * zoom) <= 1, "the rail is 60 CSS px, drawn over the app");
      for (const surface of ["artifact-preview-dock", "review-workspace", "sub-agent-dock", "profile-insights-host"]) {
        await page.evaluate(surface => {
          const el = document.createElement("aside");
          el.className = surface;
          el.innerHTML = '<button data-surface-action>Preview toolbar action</button>';
          document.body.append(el);
        }, surface);
        const bounds = await page.evaluate(surface => {
          const panel = document.querySelector('.' + surface).getBoundingClientRect();
          return { panelTop: panel.top, panelBottom: panel.bottom };
        }, surface);
        assert.ok(Math.abs(bounds.panelTop) <= 1, `${surface} starts at the top of the window: ${JSON.stringify(bounds)}`);
        assert.ok(bounds.panelBottom <= height + 1, `${surface} extends below viewport`);
        await page.locator('[data-surface-action]').click();
        await page.locator('.' + surface).evaluate(el => el.remove());
      }
      await page.close();
    }
  }
  // Web has no native chrome and must retain its full viewport.
  const page = await browser.newPage();
  await page.setContent(`<style>${css}</style><aside class="artifact-preview-dock"></aside>`);
  assert.equal((await page.locator('.artifact-preview-dock').boundingBox()).y, 0);
  await page.close();
  console.log("PASS: root-portaled surfaces share the whole window with the floating controls at normal/zoomed and short/tall viewports; web remains full-height");
} finally { await browser.close(); }
