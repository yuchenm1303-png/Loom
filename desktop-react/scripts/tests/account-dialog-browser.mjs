// The account dialog (src/components/AccountDialog.tsx, account-auth.css, docs/account-dialog-2026-10-26.md): sign-in,
// registration, the account and the profile editor in one card, on the live-app fixture's account service
// (?account=...). Every state draws inside the card in both themes; a change of view glides (the card's top stays where
// it is, its height passes through the values in between, the words fade through); what appears inside a view folds
// open; the focus stays inside the card and comes back to the dock when it closes; with reduced motion it just jumps.
//   LOOM_TEST_ORIGIN=http://127.0.0.1:5199 node scripts/tests/account-dialog-browser.mjs
//   LOOM_TEST_START_SERVER=1 node scripts/tests/account-dialog-browser.mjs        (starts its own Vite)
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const server = process.env.LOOM_TEST_START_SERVER ? await (await import("vite")).createServer({ server: { port: 0 } }) : null;
await server?.listen();
const origin = (process.env.LOOM_TEST_ORIGIN || server?.resolvedUrls.local[0] || "http://127.0.0.1:5173").replace(/\/$/, "");
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true, args: ["--no-proxy-server"] });

const AUTH_LATENCY = 250;

async function openDialog(query, { theme = "dark", reduced = false } = {}) {
  const page = await browser.newPage({ viewport: { width: 1333, height: 853 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const search = new URLSearchParams({ scenario: "code", lang: "en", theme, authLatency: String(AUTH_LATENCY), ...query });
  if (reduced) search.set("reduced", "1");
  await page.goto(`${origin}/scripts/fixtures/live-app.html?${search}`);
  const tool = page.locator('.loom-tool-button[data-tool-id="account"]');
  await tool.waitFor({ timeout: 30_000 });
  await page.waitForTimeout(400);
  await tool.click();
  await page.locator('.loom-account-backdrop[data-motion-phase="entered"]').waitFor();
  await page.waitForTimeout(450);
  return { page, errors };
}

const viewOf = (page) => page.locator(".loom-account-dialog").getAttribute("data-view");
const titleOf = (page) => page.locator(".acct-title .crossfade > :last-child").innerText();

// Everything the card draws is inside it: nothing pokes out at a side, nothing makes it scroll sideways.
async function drawsInside(page, label) {
  const found = await page.evaluate(() => {
    const card = document.querySelector(".loom-account-dialog");
    const box = card.getBoundingClientRect();
    const out = [];
    for (const element of card.querySelectorAll("*")) {
      if (element.closest("[inert]")) continue;
      const rect = element.getBoundingClientRect();
      if (!rect.width || !rect.height) continue;
      if (rect.left < box.left - 0.5 || rect.right > box.right + 0.5) out.push(`${element.className || element.tagName}`);
    }
    return { out, sideways: card.scrollWidth > card.clientWidth };
  });
  assert.deepEqual(found.out, [], `${label}: drawn outside the card`);
  assert.equal(found.sideways, false, `${label}: the card scrolls sideways`);
}

// Clicks `selector` in the page and records the card frame by frame for `ms`.
function filmClick(page, selector, ms) {
  return page.evaluate(async ({ selector, ms }) => {
    const card = document.querySelector(".loom-account-dialog");
    const frames = [];
    document.querySelector(selector).click();
    const start = performance.now();
    while (performance.now() - start < ms) {
      await new Promise(requestAnimationFrame);
      const box = card.getBoundingClientRect();
      // The view that leaves is on screen while it fades: drawn, with some opacity left.
      const leaving = card.querySelector(".acct-stage > .acct-views > .crossfade-out");
      const leavingStyle = leaving ? getComputedStyle(leaving) : null;
      frames.push({
        top: box.top,
        height: box.height,
        view: card.dataset.view,
        fading: Boolean(leaving && leavingStyle.display !== "none" && leavingStyle.visibility !== "hidden"
          && Number(leavingStyle.opacity) > 0.05 && leaving.getBoundingClientRect().height > 0),
        morphing: card.querySelector(".acct-stage").hasAttribute("data-morphing"),
      });
    }
    return frames;
  }, { selector, ms });
}

try {
  // -------------------------------------------------------------------------------------------- every state draws
  const STATES = [
    ["out", "form", "Sign in to Loom"],
    ["in", "account", "Loom Designer"],
    ["offline", "form", "Sign in to Loom"],
    ["offline-in", "account", "Loom Designer"],
    ["unconfigured", "unconfigured", "Account service isn't set up"],
    ["error", "error", "Couldn't load your account"],
  ];
  for (const theme of ["dark", "light"]) {
    for (const [account, view, title] of STATES) {
      const { page, errors } = await openDialog({ account }, { theme });
      const label = `${theme}/${account}`;
      assert.equal(await viewOf(page), view, `${label}: view`);
      assert.equal(await titleOf(page), title, `${label}: title`);
      await drawsInside(page, label);
      const look = await page.evaluate(() => {
        const card = getComputedStyle(document.querySelector(".loom-account-dialog"));
        const title = getComputedStyle(document.querySelector(".acct-title"));
        const primary = document.querySelector(".acct-button.is-primary");
        const head = document.querySelector(".acct-head").getBoundingClientRect();
        const mark = document.querySelector(".acct-mark").getBoundingClientRect();
        return {
          radius: card.borderTopLeftRadius,
          width: document.querySelector(".loom-account-dialog").getBoundingClientRect().width,
          ink: title.color,
          primary: primary ? getComputedStyle(primary).backgroundColor : null,
          centred: Math.abs((mark.left + mark.right) / 2 - (head.left + head.right) / 2),
          notice: Boolean(document.querySelector(".acct-notice-fold.is-open")),
          dot: document.querySelector(".acct-mark-dot")?.className ?? "",
        };
      });
      assert.equal(look.radius, "18px", `${label}: the modal corner`);
      assert.equal(Math.round(look.width), 400, `${label}: one width for every view`);
      assert.ok(look.centred < 1, `${label}: the identity is centred (${look.centred})`);
      // The one primary action is ink, in both themes.
      if (look.primary) assert.equal(look.primary, look.ink, `${label}: the primary action is ink`);
      assert.equal(look.notice, account.startsWith("offline"), `${label}: the reconnect notice only while offline`);
      if (view === "account") assert.match(look.dot, account === "offline-in" ? /is-offline/ : /is-online/, `${label}: status dot`);
      assert.deepEqual(errors, [], `${label}: page errors`);
      await page.close();
    }
  }

  // --------------------------------------------- signing in glides from the form to the account, the top stays put
  for (const theme of ["dark", "light"]) {
    const { page } = await openDialog({ account: "out" }, { theme });
    await page.locator("#loom-account-email").fill("designer@example.com");
    await page.locator("#loom-account-password").fill("correct horse");
    const frames = await filmClick(page, ".acct-submit", AUTH_LATENCY + 900);
    const label = `${theme}/sign-in`;
    assert.equal(frames.at(-1).view, "account", `${label}: ends on the account`);
    const tops = frames.map((frame) => frame.top);
    assert.ok(Math.max(...tops) - Math.min(...tops) < 0.75, `${label}: the card hangs from its line (top moved ${Math.max(...tops) - Math.min(...tops)}px)`);
    const from = frames[0].height;
    const to = frames.at(-1).height;
    assert.ok(from - to > 60, `${label}: the account is shorter than the form (${from} -> ${to})`);
    const between = new Set(frames.map((frame) => Math.round(frame.height)).filter((height) => height < from - 2 && height > to + 2));
    assert.ok(between.size >= 4, `${label}: the height glides (${between.size} heights in between)`);
    assert.ok(frames.some((frame) => frame.fading), `${label}: the form's words fade out over the account`);
    assert.ok(frames.some((frame) => frame.morphing) && !frames.at(-1).morphing, `${label}: the stage clips only while it morphs`);
    assert.equal(await titleOf(page), "Loom Designer", `${label}: the title is the person`);
    assert.equal(await page.locator(".acct-mark-dot.is-online").count(), 1, `${label}: signed in is a dot on the circle`);
    // The keyboard is still inside the card after the form it was in is gone.
    assert.ok(await page.evaluate(() => document.querySelector(".loom-account-dialog").contains(document.activeElement)), `${label}: focus stays inside`);
    await page.close();
  }

  // -------------------------------------------------------------- registration folds the confirmation field open
  {
    const { page } = await openDialog({ account: "out" });
    const confirmFold = page.locator(".acct-fold", { has: page.locator("#loom-account-confirm") });
    const closed = await confirmFold.evaluate((el) => ({ inert: el.inert, height: el.getBoundingClientRect().height }));
    assert.deepEqual(closed, { inert: true, height: 0 }, "login: the confirmation field is folded away and cannot be reached");
    // The focus walks the form without ever landing in the folded field, and wraps inside the card.
    const visited = [];
    for (let step = 0; step < 18; step += 1) {
      await page.keyboard.press("Tab");
      visited.push(await page.evaluate(() => {
        const active = document.activeElement;
        return { id: active?.id || active?.className || active?.tagName, inside: Boolean(active?.closest(".loom-account-dialog")) };
      }));
    }
    assert.ok(visited.every((stop) => stop.inside), `the focus stays in the card: ${JSON.stringify(visited)}`);
    assert.ok(!visited.some((stop) => stop.id === "loom-account-confirm"), "the folded field is never focused");
    assert.ok(visited.filter((stop) => stop.id === "loom-account-email").length >= 2, `Tab wraps around the card: ${visited.map((stop) => stop.id).join(" > ")}`);

    await page.locator(".acct-switch .acct-link").first().click();
    await page.waitForTimeout(450);
    const open = await confirmFold.evaluate((el) => ({ inert: el.inert, height: el.getBoundingClientRect().height }));
    assert.equal(open.inert, false, "register: the confirmation field can be reached");
    assert.ok(open.height > 50, `register: the confirmation field has made its room (${open.height})`);
    assert.equal(await titleOf(page), "Create your Loom account");
    assert.equal(await page.locator(".acct-submit .crossfade > :last-child").innerText(), "Create account");
    assert.equal(await page.locator("#loom-account-password-hint").innerText(), "At least 8 characters", "the rule sits on the label's line");

    // A mismatch is said on the confirmation field's own line; the form does not move.
    await page.locator("#loom-account-password").fill("longenough");
    const before = await page.locator(".acct-submit").boundingBox();
    await page.locator("#loom-account-confirm").fill("longenougX");
    await page.waitForTimeout(250);
    assert.equal(await page.locator("#loom-account-confirm-hint").innerText(), "Doesn't match");
    assert.equal((await page.locator(".acct-submit").boundingBox()).y, before.y, "a note on the label line does not push the form");
    await page.close();
  }

  // ------------------------------------------------------------------------------ a refused sign-in says why
  {
    const { page } = await openDialog({ account: "out" });
    await page.locator("#loom-account-email").fill("designer@example.com");
    await page.locator("#loom-account-password").fill("wrong");
    await page.locator(".acct-submit").click();
    const alert = page.locator(".loom-account-dialog [role=alert]");
    await alert.waitFor();
    assert.equal(await alert.innerText(), "Email or password is incorrect.");
    await page.waitForTimeout(400);
    assert.ok((await page.locator(".acct-fold", { has: page.locator("[role=alert]") }).boundingBox()).height > 15, "the error has made its room");
    assert.equal(await viewOf(page), "form");
    await page.close();
  }

  // --------------------------------------------- the editor: in place, the picture is a control, saving comes back
  {
    const { page } = await openDialog({ account: "in", avatar: "1" });
    await page.locator(".acct-row", { hasText: "Edit profile" }).click();
    await page.waitForTimeout(450);
    assert.equal(await viewOf(page), "editor");
    assert.equal(await page.evaluate(() => document.activeElement?.id), "loom-account-nickname", "the nickname is ready to type");
    assert.equal(await page.locator(".acct-mark-badge").count(), 1, "the picture wears the camera badge");
    assert.equal(await page.locator(".acct-mark-dot").count(), 0);
    await page.locator("#loom-account-nickname").fill("Mira");
    await page.locator(".acct-actions .acct-button.is-primary").click();
    await page.waitForFunction(() => document.querySelector(".loom-account-dialog")?.dataset.view === "account");
    await page.waitForTimeout(450);
    assert.equal(await titleOf(page), "Mira", "the saved name is the title");
    await page.close();
  }

  // ---------------------------------------------------- signing out goes back to the form, with the email focused
  {
    const { page } = await openDialog({ account: "in" });
    await page.locator(".acct-signout").click();
    await page.waitForFunction(() => document.querySelector(".loom-account-dialog")?.dataset.view === "form");
    await page.waitForTimeout(200);
    assert.equal(await page.evaluate(() => document.activeElement?.id), "loom-account-email");
    // Escape closes, and the focus goes back to the dock's account tool.
    await page.keyboard.press("Escape");
    await page.locator(".loom-account-backdrop").waitFor({ state: "detached" });
    assert.equal(await page.evaluate(() => document.activeElement?.closest(".loom-tool-button")?.dataset.toolId), "account");
    await page.close();
  }

  // --------------------------------------------------------------- reduced motion: the card simply changes size
  {
    const { page } = await openDialog({ account: "out" }, { reduced: true });
    await page.locator("#loom-account-email").fill("designer@example.com");
    await page.locator("#loom-account-password").fill("correct horse");
    const frames = await filmClick(page, ".acct-submit", AUTH_LATENCY + 500);
    assert.equal(frames.at(-1).view, "account");
    const from = frames[0].height;
    const to = frames.at(-1).height;
    const between = frames.filter((frame) => frame.height < from - 2 && frame.height > to + 2);
    assert.deepEqual(between, [], "reduced motion: no glide");
    assert.ok(!frames.some((frame) => frame.fading || frame.morphing), "reduced motion: no cross-fade, no morph");
    await page.close();
  }

  console.log("Account dialog: every state in both themes, sign-in glide, registration fold, errors, editor, sign-out, focus and reduced motion passed.");
} finally {
  await browser.close();
  await server?.close();
}
