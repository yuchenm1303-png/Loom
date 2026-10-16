// The panel language in a real browser (src/components/panel-language.css, PanelHeader.tsx).
// Every panel the live-app fixture can open is opened in both themes and its material is read back
// from the page:
//   - docks (inspector, review, sub-agents, artifacts, project): one surface, one hairline edge, no
//     shadow, a rail-high header with a 32px mark, a 14px title and a 30px quiet close button;
//   - palettes and modals (background tasks, Files, account): the same tokens, the same corners
//     (13px hanging, 18px centred), one scrim;
//   - the shells of the panels that hang off a control (model panel, permission, context menu);
//   - how they leave: nothing that appears in place is unmounted in one frame, it fades out over
//     --loom-motion-conceal and is only then removed, by opacity alone; reduced motion is immediate;
//   - the profile and settings pages reveal their content, never their canvas, by opacity alone.
// Needs the Vite fixture server (LOOM_TEST_ORIGIN, see loom-browser-tests-setup).
import assert from "node:assert/strict";
const { chromium } = await import(process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core");
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5173";
const browser = await chromium.launch({ executablePath: process.env.LOOM_CHROMIUM_PATH, headless: true });

const dock = (id) => (page) => page.locator(`.loom-tool-button[data-tool-id="${id}"]`).evaluate((el) => el.click());
const selectProject = async (page) => { await page.locator(".project-group-main").first().click(); };
const openFiles = async (page) => {
  await selectProject(page);
  await page.locator(".project-details-panel[data-motion-phase=\"entered\"]").waitFor();
  await page.locator(".project-details-close").click();
  await page.locator(".project-details-panel").waitFor({ state: "detached" });
  await dock("files")(page);
};

// Runs in the page: what a token resolves to for a property (so the test compares colours, not strings of var()).
function resolve([name, property]) {
  const probe = document.createElement("i");
  probe.style.setProperty(property, `var(${name})`);
  document.body.append(probe);
  const value = getComputedStyle(probe).getPropertyValue(property);
  probe.remove();
  return value;
}

// Runs in the page: the computed material of one element.
function material(selector) {
  const element = document.querySelector(selector);
  if (!element) return null;
  const style = getComputedStyle(element);
  const box = element.getBoundingClientRect();
  return {
    background: style.backgroundColor, image: style.backgroundImage, shadow: style.boxShadow, radius: style.borderTopLeftRadius,
    edge: style.borderLeftColor, edgeWidth: style.borderLeftWidth, line: style.borderTopColor, lineWidth: style.borderTopWidth,
    height: box.height, width: box.width, size: style.fontSize, weight: style.fontWeight, color: style.color,
  };
}

// Runs in the page: the colour a scrim paints. The desktop shell moves it onto ::before so that it can fade
// under the window controls (window-rail.css); everywhere else the host paints it.
function scrimPaint(selector) {
  const element = document.querySelector(selector);
  if (!element) return null;
  const before = getComputedStyle(element, "::before");
  return { host: getComputedStyle(element).backgroundColor, before: before.content === "none" || before.content === "normal" ? "" : before.backgroundColor };
}

const docks = [
  { name: "inspector", open: dock("inspector"), root: ".inspector.runtime-inspector", title: ".runtime-title-row strong", mark: ".runtime-mark" },
  { name: "review", open: dock("review"), root: ".review-workspace", header: ".review-topbar" },
  { name: "agents", open: dock("agents"), root: ".sub-agent-dock", header: ".sub-agent-workspace-header" },
  { name: "artifacts", open: dock("artifacts"), root: ".artifact-preview-dock", header: ".artifact-preview-header" },
  { name: "project", open: selectProject, root: ".project-details-panel", header: ".project-details-header" },
];

async function newPage(theme, extra = "") {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/scripts/fixtures/live-app.html?scenario=code&theme=${theme}&lang=en${extra}`);
  await page.locator(".thread-header").waitFor();
  await page.waitForTimeout(500);
  return { page, errors };
}

const failures = [];
const check = (label, condition, detail = "") => { if (!condition) failures.push(`${label}${detail ? `: ${detail}` : ""}`); };

try {
  for (const theme of ["dark", "light"]) {
    // ---- docks -------------------------------------------------------------------------------
    for (const panel of docks) {
      const { page, errors } = await newPage(theme);
      await panel.open(page);
      await page.waitForTimeout(1000);
      const tokens = await page.evaluate(({ resolveSource }) => {
        const resolveToken = new Function(`return (${resolveSource})`)();
        return {
          surface: resolveToken(["--pl-surface", "background-color"]), edge: resolveToken(["--pl-edge", "border-left-color"]),
          ink: resolveToken(["--pl-ink", "color"]), rail: parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--pl-header-height")) || 60,
        };
      }, { resolveSource: resolve.toString() });
      const root = await page.evaluate(material, panel.root);
      const label = `${theme} ${panel.name}`;
      check(`${label}: dock root exists`, Boolean(root));
      if (root) {
        check(`${label}: one surface`, root.background === tokens.surface && root.image === "none", `${root.background} ${root.image.slice(0, 40)} vs ${tokens.surface}`);
        check(`${label}: one hairline edge`, root.edgeWidth === "1px" && root.edge === tokens.edge, `${root.edgeWidth} ${root.edge} vs ${tokens.edge}`);
        check(`${label}: no shadow while it sits still`, root.shadow === "none", root.shadow);
        check(`${label}: no corner`, root.radius === "0px");
      }
      if (panel.header) {
        const header = await page.evaluate(material, `${panel.root} ${panel.header}.pl-header`);
        check(`${label}: header is PanelHeader`, Boolean(header));
        if (header) check(`${label}: header is as high as the rail`, header.height === tokens.rail, String(header.height));
        const mark = await page.evaluate(material, `${panel.root} .pl-header .pl-mark`);
        check(`${label}: 32px mark`, mark && Math.round(mark.width) === 32 && Math.round(mark.height) === 32, JSON.stringify(mark && [mark.width, mark.height]));
        const title = await page.evaluate(material, `${panel.root} .pl-header .pl-title`);
        check(`${label}: 14px / 650 title`, title && title.size === "14px" && title.weight === "650", JSON.stringify(title && [title.size, title.weight]));
        const subtitle = await page.evaluate(material, `${panel.root} .pl-header .pl-subtitle`);
        check(`${label}: quiet subtitle`, subtitle && (subtitle.size === "12px" || subtitle.size === "11.5px"), JSON.stringify(subtitle && subtitle.size));
        const close = await page.evaluate(material, `${panel.root} .pl-header .pl-close`);
        check(`${label}: 30px quiet close button`, close && Math.round(close.width) === 30 && Math.round(close.height) === 30 && close.background === "rgba(0, 0, 0, 0)" && close.radius === "8px",
          JSON.stringify(close && [close.width, close.height, close.background, close.radius]));
      } else {
        const title = await page.evaluate(material, `${panel.root} ${panel.title}`);
        check(`${label}: 14px / 650 title`, title && title.size === "14px" && title.weight === "650", JSON.stringify(title && [title.size, title.weight]));
        const mark = await page.evaluate(material, `${panel.root} ${panel.mark}`);
        check(`${label}: 32px mark`, mark && Math.round(mark.width) === 32 && Math.round(mark.height) === 32);
      }
      if (errors.length) failures.push(`${label}: page errors ${errors.slice(0, 2).join(" | ")}`);
      await page.close();
    }

    // ---- palettes, modals, scrims --------------------------------------------------------------
    const shells = [
      { name: "background", open: dock("background"), root: ".background-task-panel", host: ".background-task-panel", radius: "13px", header: ".background-task-topbar" },
      { name: "files", open: openFiles, root: ".files-workbench", host: ".files-workbench-backdrop", radius: "18px", header: ".files-workbench-header", scrim: true },
      { name: "account", open: dock("account"), root: ".loom-account-dialog", host: ".loom-account-backdrop", radius: "18px", scrim: true },
    ];
    for (const shell of shells) {
      const { page, errors } = await newPage(theme);
      await shell.open(page);
      await page.waitForTimeout(1000);
      const tokens = await page.evaluate(({ resolveSource }) => {
        const resolveToken = new Function(`return (${resolveSource})`)();
        return {
          raised: resolveToken(["--pl-surface-raised", "background-color"]), line: resolveToken(["--pl-line", "border-top-color"]),
          scrim: resolveToken(["--pl-scrim", "background-color"]), float: resolveToken(["--pl-shadow-float", "box-shadow"]),
          modal: resolveToken(["--pl-shadow-modal", "box-shadow"]), rail: parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--pl-header-height")) || 60,
        };
      }, { resolveSource: resolve.toString() });
      const root = await page.evaluate(material, shell.root);
      const label = `${theme} ${shell.name}`;
      check(`${label}: card exists`, Boolean(root));
      if (root) {
        check(`${label}: raised surface`, root.background === tokens.raised, `${root.background} vs ${tokens.raised}`);
        check(`${label}: ${shell.radius} corners`, root.radius === shell.radius, root.radius);
        check(`${label}: hairline`, root.lineWidth === "1px" && root.line === tokens.line, `${root.lineWidth} ${root.line} vs ${tokens.line}`);
        check(`${label}: its tier's shadow`, root.shadow === (shell.scrim ? tokens.modal : tokens.float), root.shadow);
      }
      if (shell.scrim) {
        const paint = await page.evaluate(scrimPaint, shell.host);
        check(`${label}: one scrim`, paint && (paint.host === tokens.scrim || paint.before === tokens.scrim), `${paint && JSON.stringify(paint)} vs ${tokens.scrim}`);
      }
      if (shell.header) {
        const header = await page.evaluate(material, `${shell.header}.pl-header`);
        check(`${label}: PanelHeader at rail height`, header && header.height === tokens.rail, String(header && header.height));
        const close = await page.evaluate(material, `${shell.root} .pl-header .pl-close`);
        check(`${label}: 30px quiet close button`, close && Math.round(close.width) === 30 && close.background === "rgba(0, 0, 0, 0)");
      }
      if (shell.name === "files") {
        // The workbench's accent is the product's violet; it used to be a blue of its own.
        const accent = await page.evaluate(({ resolveSource }) => {
          const resolveToken = new Function(`return (${resolveSource})`)();
          const more = document.querySelector(".files-workbench-more");
          return { token: resolveToken(["--pl-accent", "color"]), more: more ? getComputedStyle(more).color : "" };
        }, { resolveSource: resolve.toString() });
        check(`${label}: violet accent`, !accent.more || accent.more === accent.token, `${accent.more} vs ${accent.token}`);
      }
      if (errors.length) failures.push(`${label}: page errors ${errors.slice(0, 2).join(" | ")}`);
      await page.close();
    }

    // ---- the shells of what hangs off a control --------------------------------------------------
    {
      const { page, errors } = await newPage(theme);
      const hanging = [
        ["permission", async () => { await page.locator(".composer button", { hasText: /Full access/ }).first().click(); }, ".composer-popover.permission-popover"],
        ["model", async () => { await page.keyboard.press("Escape"); await page.locator(".composer button", { hasText: /MiniMax/ }).first().click(); }, ".composer-popover.model-manager-popover"],
        ["context menu", async () => { await page.keyboard.press("Escape"); await page.locator(".assistant-message").first().click({ button: "right" }); }, ".loom-context-menu"],
      ];
      for (const [name, open, selector] of hanging) {
        await open();
        await page.waitForTimeout(900);
        const tokens = await page.evaluate(({ resolveSource }) => {
          const resolveToken = new Function(`return (${resolveSource})`)();
          return { raised: resolveToken(["--pl-surface-raised", "background-color"]), shadow: resolveToken(["--pl-shadow-float", "box-shadow"]), line: resolveToken(["--pl-line", "border-top-color"]) };
        }, { resolveSource: resolve.toString() });
        const shell = await page.evaluate(material, selector);
        const label = `${theme} ${name}`;
        check(`${label}: exists`, Boolean(shell));
        if (shell) {
          check(`${label}: raised surface, no gradient`, shell.background === tokens.raised && shell.image === "none", `${shell.background} ${shell.image.slice(0, 30)}`);
          check(`${label}: 13px corners, hairline, the float shadow`, shell.radius === "13px" && shell.line === tokens.line && shell.shadow === tokens.shadow, `${shell.radius} ${shell.line} ${shell.shadow}`);
        }
      }
      if (errors.length) failures.push(`${theme} popovers: page errors ${errors.slice(0, 2).join(" | ")}`);
      await page.close();
    }
  }

  // ---- how a panel leaves: dark is enough, motion is theme-blind -------------------------------------
  const leaving = [
    { name: "background tasks", open: dock("background"), host: ".background-task-panel", close: (page) => page.locator(".background-task-panel .pl-close").click() },
    { name: "files", open: openFiles, host: ".files-workbench-backdrop", close: (page) => page.keyboard.press("Escape") },
    { name: "account", open: dock("account"), host: ".loom-account-backdrop", close: (page) => page.locator(".loom-account-close").click() },
  ];
  for (const panel of leaving) {
    const { page, errors } = await newPage("dark");
    await page.evaluate(() => {
      // One sample per painted frame: the host's phase and painted opacity, and whether it is still there.
      window.__samples = [];
      const loop = () => {
        const host = window.__host ? document.querySelector(window.__host) : null;
        const style = host ? getComputedStyle(host) : null;
        window.__samples.push({ at: performance.now(), here: Boolean(style), phase: host && host.dataset.motionPhase || "", opacity: style ? parseFloat(style.opacity) : 0,
          transform: style ? style.transform : "", property: style ? style.transitionProperty : "" });
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    });
    await page.evaluate((selector) => { window.__host = selector; window.__samples.length = 0; }, panel.host);
    await panel.open(page);
    await page.waitForTimeout(900);
    const enter = await page.evaluate(() => window.__samples.filter((sample) => sample.here));
    const label = `${panel.name} enters`;
    check(`${label}: starts transparent and ends opaque`, enter.length > 3 && enter[0].opacity < 1 && enter[enter.length - 1].opacity === 1, JSON.stringify(enter.slice(0, 2).map((s) => s.opacity)));
    check(`${label}: by opacity alone`, enter.every((sample) => sample.transform === "none" || sample.transform === ""), [...new Set(enter.map((s) => s.transform))].join(" | "));
    await page.evaluate(() => { window.__samples.length = 0; });
    const closedAt = await page.evaluate(() => performance.now());
    await panel.close(page);
    await page.waitForTimeout(700);
    const leave = await page.evaluate(() => window.__samples.map((sample) => ({ ...sample })));
    const closing = `${panel.name} leaves`;
    const stillHere = leave.filter((sample) => sample.here);
    const last = stillHere[stillHere.length - 1];
    check(`${closing}: held mounted after the click`, stillHere.some((sample) => sample.at - closedAt > 100), `last painted ${last ? Math.round(last.at - closedAt) : "never"} ms after the click`);
    check(`${closing}: fades out, in the exiting phase`, stillHere.some((sample) => sample.phase === "exiting" && sample.opacity < 1) && stillHere.some((sample) => sample.opacity < 0.5), JSON.stringify([...new Set(stillHere.map((s) => s.phase))]));
    check(`${closing}: opacity never turns back up`, stillHere.every((sample, index) => index === 0 || sample.opacity <= stillHere[index - 1].opacity + 0.001));
    check(`${closing}: removed before the next beat`, last && last.at - closedAt < 400 && !leave[leave.length - 1].here, `last painted ${last ? Math.round(last.at - closedAt) : "never"} ms after the click`);
    check(`${closing}: by opacity alone`, stillHere.every((sample) => sample.transform === "none" || sample.transform === ""));
    if (errors.length) failures.push(`${panel.name}: page errors ${errors.slice(0, 2).join(" | ")}`);
    await page.close();
  }

  // ---- reduced motion: nothing waits ---------------------------------------------------------------
  for (const panel of leaving) {
    const { page } = await newPage("dark", "&reduced=1");
    await panel.open(page);
    await page.waitForTimeout(500);
    const property = await page.evaluate((selector) => getComputedStyle(document.querySelector(selector)).transitionProperty, panel.host);
    check(`${panel.name} reduced motion: no transition`, property === "none" || property === "", property);
    await panel.close(page);
    await page.waitForTimeout(120);
    check(`${panel.name} reduced motion: gone at once`, (await page.locator(panel.host).count()) === 0);
    await page.close();
  }

  // ---- pages: the canvas stays, the content is revealed by opacity alone ---------------------------
  for (const [name, open, host, content] of [
    ["profile", dock("profile"), ".profile-insights-host", ".profile-insights-page"],
    ["settings", dock("settings"), ".settings-host", ".settings-main"],
  ]) {
    const { page } = await newPage("dark");
    await page.evaluate(([hostSelector, contentSelector]) => {
      window.__page = [];
      const loop = () => {
        const hostEl = document.querySelector(hostSelector); const contentEl = document.querySelector(contentSelector);
        if (hostEl && contentEl) window.__page.push({ host: parseFloat(getComputedStyle(hostEl).opacity), content: parseFloat(getComputedStyle(contentEl).opacity), transform: getComputedStyle(contentEl).transform });
        requestAnimationFrame(loop);
      };
      requestAnimationFrame(loop);
    }, [host, content]);
    await open(page);
    await page.waitForTimeout(900);
    const samples = await page.evaluate(() => window.__page);
    check(`${name} page: canvas opaque from the first frame`, samples.length > 3 && samples.every((sample) => sample.host === 1), JSON.stringify([...new Set(samples.map((s) => s.host))]));
    check(`${name} page: content is revealed`, samples.length > 3 && samples[0].content < 1 && samples[samples.length - 1].content === 1, JSON.stringify([samples[0]?.content, samples[samples.length - 1]?.content]));
    check(`${name} page: by opacity alone`, samples.every((sample) => sample.transform === "none"), [...new Set(samples.map((s) => s.transform))].join(" | "));
    await page.close();
  }

  assert.deepEqual(failures, [], `\n${failures.join("\n")}`);
  console.log("PASS: panel language - docks, palettes, modals and hanging panels share material and header; everything that appears in place leaves again by opacity; pages reveal their content only");
} finally { await browser.close(); }
