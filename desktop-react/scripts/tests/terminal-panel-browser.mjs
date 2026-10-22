import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const { chromium } = await import(
  process.env.LOOM_PLAYWRIGHT_MODULE || "playwright-core"
);
const python =
  process.env.LOOM_TEST_PYTHON ||
  fileURLToPath(
    new URL(
      "../../../.cache/terminal-venv/Scripts/python.exe",
      import.meta.url,
    ),
  );
const child = spawn(
  python,
  [fileURLToPath(new URL("../fixtures/terminal-host.py", import.meta.url))],
  { stdio: ["ignore", "pipe", "pipe"], windowsHide: true },
);
let server;
const port = await new Promise((resolve, reject) => {
  const timeout = setTimeout(
    () => reject(new Error("PTY host startup timed out")),
    20000,
  );
  child.once("exit", (code) => {
    clearTimeout(timeout);
    reject(new Error(`PTY host exited: ${code}`));
  });
  child.stderr.on("data", (chunk) => process.stderr.write(chunk));
  const lines = createInterface({ input: child.stdout });
  lines.once("line", (line) => {
    clearTimeout(timeout);
    resolve(JSON.parse(line).port);
  });
});
server = `http://127.0.0.1:${port}`;
let writeAcknowledgementDelay = 0;
let writeRequests = 0;
const rpc = async (method, params = {}) => {
  if (method === "terminal/session/write") writeRequests += 1;
  const response = await fetch(server, {
    method: "POST",
    body: JSON.stringify({ method, params }),
  });
  const value = await response.json();
  if (value.error) throw new Error(value.error);
  if (method === "terminal/session/write" && writeAcknowledgementDelay)
    await new Promise((resolve) =>
      setTimeout(resolve, writeAcknowledgementDelay),
    );
  return value.result;
};
const browser = await chromium.launch({
  executablePath: process.env.LOOM_CHROMIUM_PATH,
  headless: true,
});
const origin = process.env.LOOM_TEST_ORIGIN || "http://127.0.0.1:5175";
const errors = [];
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.exposeFunction("terminalRpc", rpc);
  const inject = async () =>
    page.evaluate(() => {
      const call = window.loom.call;
      window.loom.call = (method, params) =>
        method.startsWith("terminal/")
          ? window.terminalRpc(method, params)
          : call(method, params);
    });
  await page.goto(`${origin}/scripts/fixtures/live-app.html?lang=en`);
  await page.locator('.loom-tool-button[data-tool-id="terminal"]').waitFor();
  await inject();
  await page.locator('.loom-tool-button[data-tool-id="terminal"]').click();
  await page.locator(".terminal-toolbar .terminal-new").click();
  await page
    .locator(".terminal-emulator:not([hidden]) .xterm-helper-textarea")
    .waitFor();
  const input = () =>
    page.locator(".terminal-emulator:not([hidden]) .xterm-helper-textarea");
  const visibleOutput = () =>
    page.locator(".terminal-emulator:not([hidden]) .xterm-accessibility-tree");
  // Real PowerShell/ConPTY or POSIX PTY, not a scripted terminal transcript.
  await input().focus();
  await page.keyboard.type("echo LOOM_REAL_TERMINAL");
  await page.keyboard.press("Enter");
  await visibleOutput()
    .getByText("LOOM_REAL_TERMINAL", { exact: true })
    .waitFor({ timeout: 20000 });
  // An 80ms IPC acknowledgement must not turn a 30-character command into
  // 30 serial round trips. Measure actual shell output, with no local echo.
  writeAcknowledgementDelay = 80;
  const writesBefore = writeRequests;
  const started = performance.now();
  await page.keyboard.type("echo LOOM_LOW_LATENCY_0123456789");
  await page.keyboard.press("Enter");
  // Accessibility announcements intentionally debounce for a second; inspect
  // the visible DOM renderer, which is what the user actually sees.
  await page.waitForFunction(
    () =>
      [
        ...document.querySelectorAll(
          ".terminal-emulator:not([hidden]) .xterm-rows > div",
        ),
      ].some((row) => row.textContent.trim() === "LOOM_LOW_LATENCY_0123456789"),
    null,
    { polling: "raf" },
  );
  const elapsed = performance.now() - started;
  const batchedWrites = writeRequests - writesBefore;
  console.log(
    JSON.stringify({
      terminalCommandEchoMs: Math.round(elapsed),
      writeAcknowledgementMs: 80,
      batchedWrites,
    }),
  );
  assert.ok(
    elapsed < 700,
    `typed input accumulated round-trip delays: ${elapsed}ms`,
  );
  assert.ok(
    batchedWrites < 10,
    `input was sent as ${batchedWrites} separate key requests`,
  );
  writeAcknowledgementDelay = 0;
  await page.keyboard.type(
    process.platform === "win32"
      ? '$env:LOOM_TERMINAL_TEST="retained"'
      : "export LOOM_TERMINAL_TEST=retained",
  );
  await page.keyboard.press("Enter");
  await page
    .getByRole("button", {
      name: "Hide terminal (sessions keep running)",
      exact: true,
    })
    .click();
  assert.equal(await page.locator(".terminal-panel").isVisible(), false);
  await page.keyboard.press("Control+Backquote");
  await page.locator(".terminal-panel").waitFor();
  await input().focus();
  await page.keyboard.type(
    process.platform === "win32"
      ? "echo $env:LOOM_TERMINAL_TEST"
      : "echo $LOOM_TERMINAL_TEST",
  );
  await page.keyboard.press("Enter");
  await visibleOutput().getByText("retained", { exact: true }).waitFor();
  // Ctrl+C interrupts a foreground command and leaves the shell usable.
  await page.keyboard.type(
    process.platform === "win32" ? "Start-Sleep -Seconds 30" : "sleep 30",
  );
  await page.keyboard.press("Enter");
  await page.waitForTimeout(400);
  await page.keyboard.press("Control+c");
  await page.waitForTimeout(300);
  await page.keyboard.type("echo AFTER_INTERRUPT");
  await page.keyboard.press("Enter");
  await visibleOutput().getByText("AFTER_INTERRUPT", { exact: true }).waitFor();
  await page
    .getByRole("button", { name: "Rename session", exact: true })
    .click();
  await page.getByRole("textbox", { name: "Session name" }).fill("Development");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByRole("tab", { name: "Development" }).waitFor();
  await page.getByRole("button", { name: "Maximize terminal" }).click();
  await page.waitForTimeout(250);
  const listed = await rpc("terminal/session/list");
  assert.ok(listed.sessions[0].cols > 120, "maximizing resizes the actual PTY");
  await page.getByRole("button", { name: "Maximize terminal" }).click();
  await page
    .getByRole("button", { name: "Find in terminal", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Search terminal output" })
    .fill("retained");
  await page.keyboard.press("Enter");
  assert.equal(await page.getByText("No results", { exact: true }).count(), 0);
  await page.getByRole("button", { name: "Close search" }).click();
  await page.locator(".terminal-toolbar .terminal-new").click();
  await page.waitForFunction(
    () => document.querySelectorAll(".terminal-tab").length === 2,
  );
  assert.equal(
    (await rpc("terminal/session/list")).sessions.filter((s) => s.running)
      .length,
    2,
  );
  await page.locator(".terminal-tab-close").last().click();
  await page.waitForFunction(
    () => document.querySelectorAll(".terminal-tab").length === 1,
  );
  // A command sent from a code block opens a real command session.
  await page.evaluate(() =>
    window.dispatchEvent(
      new CustomEvent("loom:open-terminal", {
        detail: {
          workspace: "C:/Workspace/Loom",
          command: 'print("CODE_BLOCK_RESULT")',
          language: "python",
        },
      }),
    ),
  );
  await visibleOutput()
    .getByText("CODE_BLOCK_RESULT", { exact: true })
    .waitFor();
  await page.waitForFunction(() =>
    document.querySelector(".terminal-footer")?.textContent.includes("Exited"),
  );
  // Exercise the actual MarkdownMessage controls, including independent readers
  // looking at the same process. Opening output must not execute code twice.
  await page
    .getByRole("button", {
      name: "Hide terminal (sessions keep running)",
      exact: true,
    })
    .click();
  await page.evaluate(async () => {
    const fixture = await import("/scripts/fixtures/terminal-codes.tsx");
    window.removeTerminalCodes = fixture.mountTerminalCodes();
  });
  const codeBlocks = page.locator(
    "#terminal-code-fixture .markdown-code-block",
  );
  await codeBlocks
    .first()
    .getByRole("button", { name: "Run command", exact: true })
    .click();
  await codeBlocks
    .first()
    .locator(".terminal-run-output")
    .filter({ hasText: "INLINE_CODE_RESULT" })
    .waitFor();
  const before = (await rpc("terminal/session/list")).sessions.length;
  assert.equal(
    await codeBlocks.nth(2).locator(".terminal-run-actions").count(),
    0,
    "SQL and non-executable code never get a misleading Run button",
  );
  await codeBlocks
    .nth(1)
    .getByRole("button", { name: "Run command", exact: true })
    .click();
  await codeBlocks
    .nth(1)
    .locator(".terminal-run-output")
    .filter({ hasText: "JS_CODE_RESULT" })
    .waitFor();
  await codeBlocks
    .first()
    .getByRole("button", { name: "Run in terminal", exact: true })
    .click();
  await page.locator(".terminal-panel").waitFor();
  assert.equal(
    (await rpc("terminal/session/list")).sessions.length,
    before + 1,
    "opening inline output reuses its process",
  );
  await visibleOutput()
    .getByText("INLINE_CODE_RESULT", { exact: true })
    .waitFor();
  await page.evaluate(() => {
    window.removeTerminalCodes();
  });
  // Both themes and a narrow layout keep all controls and the PTY in bounds.
  for (const theme of ["light", "dark"]) {
    await page.evaluate(
      (theme) => (document.documentElement.dataset.loomTheme = theme),
      theme,
    );
    await page.setViewportSize({ width: 540, height: 800 });
    const rect = await page.locator(".terminal-panel").boundingBox();
    assert.ok(
      rect.x >= 0 && rect.x + rect.width <= 540 && rect.y + rect.height <= 800,
    );
    assert.equal(
      await page.locator(".terminal-panel").evaluate((el) => {
        const rect = el.getBoundingClientRect();
        return el.contains(document.elementFromPoint(rect.x + 18, rect.y + 18));
      }),
      true,
      "sidebar and workspace stacking cannot cover terminal controls",
    );
    await page.waitForTimeout(120);
    assert.equal(
      await page.locator('.terminal-notice[role="alert"]').count(),
      0,
      "replaying an exited PTY must not try to write automatic terminal responses",
    );
    if (process.env.LOOM_TERMINAL_SCREENSHOTS)
      await page.screenshot({
        path: `${process.env.LOOM_TERMINAL_SCREENSHOTS}/terminal-${theme}.png`,
      });
  }
  assert.deepEqual(errors, []);
  console.log(
    "Real PTY terminal UI passed: command input, output, environment retention, hide/reopen, Ctrl+C, rename, multiple sessions, close, resize, search, command sessions and narrow themes.",
  );
} finally {
  for (const session of (await rpc("terminal/session/list")).sessions)
    await rpc("terminal/session/close", { processId: session.processId });
  await browser.close();
  child.kill();
}
