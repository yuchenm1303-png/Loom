// 对话 and 建造 as two separate spaces: the sidebar's list, where the switch lands, new, archive and delete staying put.
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const BUILD = ["visual-1", "visual-2", "visual-3"];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(ids => {
    if (localStorage.getItem("loom.projectMode")) return;
    localStorage.setItem("loom.projectMode", JSON.stringify(ids));
    localStorage.setItem("loom.sidebar.expandedProjects", JSON.stringify(["loom", "agent", "relay"]));
  }, BUILD);
  await page.goto(`${origin}/scripts/fixtures/workspace-visual.html?theme=dark&motion=1`);
  const toggle = page.locator(".thread-header-actions .pf-toggle");
  await toggle.waitFor();
  const rows = () => page.locator(".compact-thread-row").evaluateAll(nodes => nodes.map(node => node.dataset.threadId));
  const active = () => page.locator(".compact-thread-row.active").getAttribute("data-thread-id");
  // The hub draws once its overview has loaded.
  const inHub = () => page.locator(".project-mode-stage .pf-composer").waitFor({ timeout: 3000 }).then(() => true, () => false);
  const settled = () => page.waitForFunction(() => !document.querySelector(".mode-shell, [data-morph], [data-mode-morph]"), null, { timeout: 5000 });
  const go = async (space) => { await toggle.getByRole("radio").nth(space === "chat" ? 0 : 1).click(); await page.waitForTimeout(50); await settled(); };
  const open = async (id) => {
    await page.locator(`.compact-thread-row[data-thread-id="${id}"] .compact-thread-main`).click({ position: { x: 12, y: 14 } });
    await page.waitForFunction(id => document.querySelector(".compact-thread-row.active")?.getAttribute("data-thread-id") === id, id);
  };

  await page.locator(".compact-thread-row").first().waitFor();
  const chatRows = await rows();
  assert.ok(chatRows.length && chatRows.every(id => !BUILD.includes(id)), `the chat list holds no build conversation: ${chatRows}`);
  const lastChat = chatRows[1] ?? chatRows[0];
  await open(lastChat);

  // The shell carries the composer across, and the landing leaves nothing behind.
  await toggle.getByRole("radio").nth(1).click();
  await page.waitForSelector(".mode-shell", { timeout: 2000, state: "attached" });
  await settled();
  assert.ok(await inHub(), "switching to 建造 shows the hub");
  const buildRows = await rows();
  assert.ok(buildRows.length && buildRows.every(id => BUILD.includes(id)), `the build list holds only build conversations: ${buildRows}`);
  assert.ok(BUILD.includes(await active()), "the switch lands on a build conversation");
  assert.ok(await page.locator(".project-section .sidebar-section-title").filter({ hasText: /Build projects|建造项目/ }).isVisible());

  // Each space remembers its own conversation.
  const pick = buildRows.find(id => id !== buildRows[0]) ?? buildRows[0];
  await open(pick);
  await go("chat");
  assert.equal(await active(), lastChat, "back in 对话, its last conversation");
  assert.ok(!(await rows()).some(id => BUILD.includes(id)));
  assert.ok(await page.locator(".conversation-stage").isVisible());
  await go("project");
  assert.equal(await active(), pick, "back in 建造, its last conversation");
  assert.equal(await page.evaluate(() => window.modeFixture.starts.length), 0, "switching never creates while the space has a conversation");

  // New means new in this space, once.
  await page.locator(".sidebar-new-conversation").click();
  await page.waitForFunction(() => document.querySelector(".compact-thread-row.active")?.getAttribute("data-thread-id")?.startsWith("mode-draft"));
  assert.ok(await inHub(), "a new build conversation opens in the hub, not on the chat home page");
  await page.locator(".sidebar-new-conversation").click();
  await page.waitForTimeout(400);
  assert.equal(await page.evaluate(() => window.modeFixture.starts.length), 1, "asking again from an empty one stays there");

  // Archiving the open build conversation lands on another build conversation.
  await open(pick);
  const row = page.locator(`.compact-thread-row[data-thread-id="${pick}"]`);
  await row.hover();
  await row.locator(".thread-quick-actions button").nth(1).click();
  await page.waitForFunction(id => { const now = document.querySelector(".compact-thread-row.active")?.getAttribute("data-thread-id"); return now && now !== id; }, pick);
  assert.ok(await inHub(), "archiving stays in 建造");

  // Deleting, down to the last one, stays too: the last one's deletion starts a new build rather than falling out to chat.
  let removed = 0;
  for (;;) {
    const left = await rows();
    const current = await active();
    await page.locator(`.compact-thread-row[data-thread-id="${current}"]`).click({ button: "right", position: { x: 12, y: 14 } });
    const remove = page.getByRole("menuitem", { name: /Delete conversation|Click again to delete|删除会话|再次点击确认删除/ });
    await remove.click();
    await remove.click();
    await page.waitForFunction(id => {
      const now = document.querySelector(".compact-thread-row.active")?.getAttribute("data-thread-id");
      return now && now !== id;
    }, current);
    assert.ok(await inHub(), "deleting stays in 建造");
    removed += 1;
    if (left.length === 1) break;
  }
  const starts = await page.evaluate(() => window.modeFixture.starts.length);
  assert.equal(starts, 2, `only the last deletion created a build conversation (deleted ${removed})`);
  assert.deepEqual(errors, []);
  console.log("Two spaces: separate lists, each lands on its own last conversation, new/archive/delete stay in the space");
} finally { await browser.close(); }
