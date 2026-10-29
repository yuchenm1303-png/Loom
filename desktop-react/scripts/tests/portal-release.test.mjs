import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";

const source = readFileSync(new URL("../../src/portalRelease.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const model = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const release = { tag_name: "v0.1.13", assets: [{ name: "Loom-Setup-x64.exe", size: 144284058,
  browser_download_url: `${model.RELEASES_URL}/download/v0.1.13/Loom-Setup-x64.exe` }] };

test("metadata and installer all refer to the same release", () => {
  const result = model.parsePortalRelease(release);
  assert.equal(result.version, "v0.1.13");
  assert.equal(result.package, "137.6 MiB");
  assert.equal(result.download, release.assets[0].browser_download_url);
  assert.equal(result.notes, `${model.RELEASES_URL}/tag/v0.1.13`);
});

test("rejects incomplete, prerelease and unrelated installer URLs", () => {
  for (const payload of [null, {}, { ...release, prerelease: true }, { ...release, assets: [] },
    { ...release, assets: [{ ...release.assets[0], browser_download_url: "https://example.com/setup.exe" }] }]) {
    assert.throws(() => model.parsePortalRelease(payload));
  }
  assert.equal(model.FALLBACK_RELEASE.version, "");
  assert.equal(model.FALLBACK_RELEASE.package, "");
  assert.equal(model.FALLBACK_RELEASE.download, model.LATEST_INSTALLER_URL);
});

test("request skips cache and handles API failure", async () => {
  const original = globalThis.fetch;
  try {
    const signal = new AbortController().signal;
    globalThis.fetch = async (url, options) => {
      assert.equal(url, model.RELEASE_API_URL);
      assert.equal(options.cache, "no-store");
      assert.equal(options.signal, signal);
      return { ok: true, json: async () => release };
    };
    assert.equal((await model.fetchPortalRelease(signal)).version, "v0.1.13");
    globalThis.fetch = async () => ({ ok: false, status: 403 });
    await assert.rejects(model.fetchPortalRelease(signal));
  } finally { globalThis.fetch = original; }
});


test("editorial route is the default and the classic portal remains reachable", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  assert.ok(portal.includes('=== "/classic" ? "classic" : "editorial"'));
  assert.ok(portal.includes('next === "classic" ? "/classic" : "/"'));
  assert.ok(portal.includes("loom-editorial"));
  assert.ok(portal.includes("loom-classic-switch"));
  assert.ok(portal.includes("editorial-panel-backdrop"));
  assert.ok(portal.includes("usePortalStyles(!editorial)"));
});

test("both homepage designs reuse the existing authentication and Host gate", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const gate = readFileSync(new URL("../../src/components/WebAppGate.tsx", import.meta.url), "utf8");
  for (const key of ["account.login(", "account.registerStart(", "account.verifyEmail(",
    "account.forgotPassword(", "account.resetPassword(", "account.oauthExchange(",
    "<HostSetupActions", "onOpenProfile", "fetchPortalRelease("]) {
    assert.ok(portal.includes(key), "Missing shared portal function: " + key);
  }
  assert.ok(gate.includes("<WebPortal"));
  assert.ok(gate.includes("onEnter={enterOrRetry}"));
  assert.ok(gate.includes("onOpenProfile={openProfile}"));
});

test("the introduction homepage keeps task entry inside the workspace", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const home = portal.slice(portal.indexOf('<main className="editorial-body">'), portal.indexOf('<footer className="editorial-footer'));
  assert.ok(home.includes('onClick={openWorkspace}'));
  assert.ok(home.includes('href={release.download}'));
  assert.ok(home.includes('editorial-capabilities'));
  assert.ok(!/<(?:input|textarea|form)\b/.test(home), "homepage introduces Loom without a chat composer");
  assert.ok(!portal.includes('pendingPrompt && authenticated'), "connecting must not auto-submit a homepage task");
});

test("both homepage bundles use the unmodified reference refraction shader", async () => {
  const { createHash } = await import("node:crypto");
  const source = readFileSync(new URL("../../src/components/liquid-glass-reference/shaders.ts", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  const shader = source.match(/export const liquidFragmentShader = `([\s\S]*?)`;/)[1];
  const manifest = JSON.parse(readFileSync(new URL("../../src/components/liquid-glass-reference/provenance.json", import.meta.url), "utf8"));
  const vertex = source.match(/export const liquidVertexShader = `([\s\S]*?)`;/)[1];
  assert.equal(createHash("sha256").update(vertex).digest("hex"), manifest.vertexSha256);
  assert.equal(createHash("sha256").update(shader).digest("hex"), manifest.fragmentSha256);
  assert.equal(manifest.fragmentSha256, "fd113354e2a5c49e2af467464259b67752e1379fdf348179b3ce4fe07ec9470a");
  const standalone = readFileSync(new URL("../../../artifacts/loom-site/assets/cursor.js", import.meta.url), "utf8").replace(/\r\n/g, "\n");
  assert.ok(standalone.includes(shader), "the standalone bundle must carry the exact reference shader too");
});


test("editorial sign-in is a centered, accessible modal without a side drawer", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../../src/components/portal-auth-dialog.css", import.meta.url), "utf8");
  assert.ok(portal.includes('import "./portal-auth-dialog.css"'), "dialog styles load after editorial styles");
  assert.ok(portal.includes('aria-labelledby={editorial && drawerOpen ? "editorial-dialog-title" : undefined}'));
  assert.ok(portal.includes('id="editorial-dialog-title"'));
  assert.ok(portal.includes("editorial-modal-brand"));
  assert.ok(portal.includes("data-auth-step={authStep}"));
  assert.ok(portal.includes('event.key === "Escape"'));
  assert.ok(portal.includes('event.key === "Tab"'));
  assert.ok(portal.includes("opener.focus({ preventScroll: true })"));
  assert.match(styles, /justify-content:\s*center/);
  assert.match(styles, /align-items:\s*center/);
  assert.match(styles, /flex-direction:\s*row/); // The modal flex-basis must size width, never height.
  assert.match(styles, /max-width:\s*472px/);
  assert.match(styles, /max-height:\s*calc\(100dvh - 40px\)/);
  assert.ok(styles.includes('[data-auth-step="form"]'), "recovery and verification copy should stay visible");
  assert.ok(styles.includes(".loom-host-refined"), "existing Host connection controls remain styled");
  assert.ok(styles.includes(".loom-account-refined"), "existing account controls remain styled");
  assert.ok(!styles.includes("justify-content: flex-end"), "do not reintroduce the right-hand drawer");
  assert.ok(portal.includes('next === "classic" ? "/classic" : "/"'), "classic homepage stays supported");
});


test("editorial account dialogs display only the centered Smirel wordmark", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../../src/components/portal-auth-dialog.css", import.meta.url), "utf8");
  const brand = portal.split('<div className="editorial-modal-brand" aria-label="Smirel">')[1]?.split("</div>")[0];
  assert.ok(brand?.includes('alt="Smirel"'), "Smirel logo remains visible");
  assert.ok(!brand?.includes("Loom") && !brand?.includes("editorial-modal-brand-divider"),
    "no Loom title or separator in the account modal brand");
  assert.ok(css.includes(".loom-editorial .editorial-modal-brand img"),
    "the single logo retains its centered sizing");
  assert.ok(!css.includes(".editorial-modal-brand-divider"),
    "remove unused divider styles");
});

test("editorial dialog prevents background focus and restores its actual opener", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../../src/components/portal-auth-dialog.css", import.meta.url), "utf8");
  assert.ok(portal.includes('inert={drawerOpen}'), "background must be inert while login is visible");
  assert.ok(portal.includes("modalOpenerRef.current = document.activeElement"), "remember opener before inert renders");
  assert.ok(portal.includes("const opener = modalOpenerRef.current"), "restoration must use pre-render focus");
  assert.ok(portal.includes("onClick={openEditorialModal}"), "Sign in must capture focus origin");
  assert.match(css, /\.loom-portal-page\.is-modular\.loom-editorial\.is-modal-open\s*\{\s*overflow:\s*hidden/);
});


test("original Loom artwork is the rightmost editorial header action", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../../src/components/portal-home-icon.css", import.meta.url), "utf8");
  const icon = readFileSync(new URL("../../public/loom-icon.png", import.meta.url));
  const header = portal.slice(portal.indexOf('<nav className="editorial-meta"'), portal.indexOf("</nav>", portal.indexOf('<nav className="editorial-meta"')));
  assert.ok(header.indexOf("editorial-home-icon") > header.indexOf("editorial-theme"), "icon belongs at the far right");
  assert.ok(header.includes('src="/loom-icon.png"'), "render the restored original asset");
  assert.ok(header.includes('onClick={openWorkspace}'), "icon opens the workspace entry flow");
  assert.ok(header.includes('aria-label={zh ? "打开 Loom 工作区" : "Open Loom workspace"}'));
  assert.equal(icon.subarray(0, 8).toString("hex"), "89504e470d0a1a0a", "copied asset must be a real PNG");
  assert.ok(css.includes(".editorial-meta .editorial-home-icon"), "header icon must have explicit layout");
  assert.ok(portal.includes('import "./portal-home-icon.css";'), "keep icon layout independent of homepage CSS rewrites");
  assert.ok(portal.includes('next === "classic" ? "/classic" : "/"'), "classic route stays intact");
});

test("the glass cursor keeps the control it is mounted on readable", () => {
  const cursor = readFileSync(new URL("../../src/components/PortalLiquidCursor.tsx", import.meta.url), "utf8");
  const optics = readFileSync(new URL("../../src/components/portalLiquidOptics.ts", import.meta.url), "utf8");

  assert.ok(!optics.includes("lensBendStrength"), "do not override the restored reference strength");
  assert.ok(optics.includes("settings.set([0, LIQUID_OPTICS.strength, LIQUID_OPTICS.pinch"));

  // Every surface is resized together: the texture is made of the capture
  // canvas, and a capture left at another size smears the control it shows.
  for (const surface of ["canvas", "capture", "scratch"]) {
    assert.ok(cursor.includes(`${surface}.width === nextWidth && ${surface}.height === nextHeight`),
      `the ${surface} surface is measured before it is skipped`);
  }
  assert.ok(cursor.includes("for (const target of [canvas, capture, scratch])"), "all three surfaces are resized together");

  // A capture window that moved is refreshed in the same frame, and it
  // follows the target's place on the page.
  assert.ok(cursor.includes("if (!moved && now - lastCapture < minCaptureInterval) return;"),
    "a moved capture window is not held back by the capture interval");
  assert.ok(cursor.includes("Math.abs(baseCenterX - roiLockedBaseX) >= 1"),
    "the capture window follows the target when the page reflows");
});


test("OAuth buttons retain magnetic wobble without overlapping borders", async () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const cursor = readFileSync(new URL("../../src/components/PortalLiquidCursor.tsx", import.meta.url), "utf8");
  const motion = readFileSync(new URL("../../src/components/portalLiquidMotion.ts", import.meta.url), "utf8");
  for (const provider of ["google", "github"]) {
    assert.ok(portal.includes(`onClick={() => startOauth("${provider}")}`), "Preserve " + provider + " sign-in");
  }
  assert.ok(!portal.includes('className="loom-oauth-button" data-magnetic-hover="false"'),
    "OAuth buttons must retain their elastic magnetic hover");
  assert.ok(cursor.includes('constrainAdjacentMagneticOffsets(left.nextX, right.nextX, originalGap)'),
    "Only the inward motion between adjacent OAuth buttons should be limited");
  assert.ok(cursor.includes('stepSpring(state.x, dt, 250, 21)'),
    "Keep the existing horizontal spring");
  assert.ok(cursor.includes('".loom-oauth-button:not(:disabled)"'),
    "OAuth actions must stay valid lens snap targets");

  const compiled = ts.transpileModule(motion, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const { constrainAdjacentMagneticOffsets: guard } = await import(
    `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
  );
  for (const [left, right, originalGap] of [[7, -8.5, 10], [9, -7, 10], [12, 0, 10], [0, -12, 10]]) {
    const limited = guard(left, right, originalGap);
    assert.ok(originalGap + limited.rightX - limited.leftX >= 6,
      "The two button borders must retain at least 6px during crossover");
    assert.ok(limited.leftX !== 0 || limited.rightX !== 0,
      "Restoring wobble must not freeze both buttons");
  }
  assert.deepEqual(guard(-8, 9, 10), { leftX: -8, rightX: 9 },
    "Moving away from a neighbor keeps the full spring motion");
  assert.deepEqual(guard(0, 0, 10), { leftX: 0, rightX: 0 });
});

test("glass capture includes native input selection, caret blink and password masking", () => {
  const capture = readFileSync(new URL("../../src/components/portalLiquidCapture.ts", import.meta.url), "utf8");
  const editable = readFileSync(new URL("../../src/components/portalLiquidEditable.ts", import.meta.url), "utf8");
  const cursor = readFileSync(new URL("../../src/components/PortalLiquidCursor.tsx", import.meta.url), "utf8");
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");

  assert.ok(capture.includes("drawEditableField(ctx, el, rect, style, roiLeft, roiTop, opacity, caretOn)"),
    "render native editing state in the same texture as text");
  assert.ok(editable.includes("el.selectionStart") && editable.includes("el.selectionEnd"),
    "read the browser's real native selection range");
  assert.ok(editable.includes("selectionBackground") && editable.includes("ctx.fillRect(selectionX"),
    "paint the selected text highlight before the text glyphs");
  assert.ok(editable.includes("style.caretColor") && editable.includes("ctx.fillRect(caretX"),
    "paint caret using the input's native caret color");
  assert.ok(editable.includes('el.type === "password"') && editable.includes('"•".repeat(value.length)'),
    "password capture may only paint masked glyphs");
  assert.ok(cursor.includes('document.addEventListener("selectionchange", handleSelection)') &&
    cursor.includes("window.clearInterval(caretBlinkTimer)"),
    "observe selection and blink without leaking the repaint interval");
  assert.ok(portal.includes('type="text" inputMode="email" pattern='),
    "email keyboard and validation must remain usable while exposing selection ranges");
});

test("verification submit mounts the lens while disabled without bypassing validation", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const cursor = readFileSync(new URL("../../src/components/PortalLiquidCursor.tsx", import.meta.url), "utf8");
  const verify = portal.split('Verify & create account')[0].slice(-550);
  assert.ok(verify.includes('data-liquid-snap="true" data-liquid-snap-disabled="true"'),
    "verification submit opts into a visual lens stop while awaiting the 6-digit code");
  assert.ok(verify.includes('disabled={account.busy || verificationCode.length !== 6}'),
    "verification submission must still reject an incomplete code");
  assert.ok(cursor.includes('element.dataset.liquidSnapDisabled === "true"'),
    "the lens accepts the explicitly marked disabled stop");
  assert.ok(cursor.includes('action.matches(":disabled, [aria-disabled='),
    "snapped click forwarding must never invoke a disabled action");
});

test("release notes use a legible centered liquid mount without enlarging the link itself", async () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const source = readFileSync(new URL("../../src/components/portalLiquidMount.ts", import.meta.url), "utf8");
  for (const [width, height] of [[136, 50], [158, 54], [150, 54]]) {
    assert.ok(portal.includes(`data-liquid-snap-min-width="${width}" data-liquid-snap-min-height="${height}"`),
      "release note mounts should stay compact and context-sensitive");
  }
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const { paddedMount } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
  const NativeRect = globalThis.DOMRect;
  globalThis.DOMRect = class DOMRect {
    constructor(x, y, width, height) {
      this.x = x; this.y = y; this.width = width; this.height = height;
      this.left = x; this.top = y;
    }
  };
  try {
    const rect = { left: 230, top: 100, width: 92, height: 18 };
    const shortLink = { dataset: { liquidSnapMinWidth: "150", liquidSnapMinHeight: "54" } };
    const frame = paddedMount(shortLink, rect);
    assert.equal(frame.width, 150);
    assert.equal(frame.height, 54);
    assert.equal(frame.x + frame.width / 2, rect.left + rect.width / 2);
    assert.equal(frame.y + frame.height / 2, rect.top + rect.height / 2);
    const normal = paddedMount({ dataset: {} }, rect);
    assert.equal(normal.width, 112, "unrelated controls retain original lens width");
    assert.equal(normal.height, 38, "unrelated controls retain original lens height");
  } finally {
    if (NativeRect === undefined) delete globalThis.DOMRect;
    else globalThis.DOMRect = NativeRect;
  }
});


test("release notes get a restrained one-shot magnetic spring without moving adjacent labels", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const cursor = readFileSync(new URL("../../src/components/PortalLiquidCursor.tsx", import.meta.url), "utf8");
  assert.equal((portal.match(/data-magnetic-bounce="true"/g) ?? []).length, 3,
    "Only the three compact release-notes links use the subtle entrance bounce");
  assert.ok(cursor.includes('element.dataset.magneticBounce === "true" ? 4.5'),
    "Compact release links keep pointer-follow motion below other controls");
  assert.ok(cursor.includes('magnetic.y.velocity = Math.min(magnetic.y.velocity, -110)'),
    "Entering a release link injects one damped vertical spring impulse");
  assert.ok(cursor.includes('owner !== previousOwner'),
    "The impulse must be linked to a mount change rather than every frame");
  const css = readFileSync(new URL("../../src/components/portal-editorial.css", import.meta.url), "utf8");
  assert.match(css, /\.editorial-release a\[data-magnetic-bounce="true"\]\s*\{\s*display:\s*inline-block/,
    "The short inline release link needs a transformable box for visible wobble");
});


test("web login card uses Turnstile only when public sitekey is advertised", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const widget = readFileSync(new URL("../../src/components/TurnstileChallenge.tsx", import.meta.url), "utf8");
  const bridge = readFileSync(new URL("../../src/webBridge.ts", import.meta.url), "utf8");
  const compose = readFileSync(new URL("../../../services/loom_web_gateway/docker-compose.yml", import.meta.url), "utf8");
  assert.ok(portal.includes("account.capabilities.turnstileSiteKey"));
  assert.ok(portal.includes("turnstileRequired && !turnstileToken"));
  assert.ok(portal.includes("account.login(submittedEmail, submittedPassword, turnstileToken)"));
  assert.ok(portal.includes("account.registerStart(submittedEmail, submittedPassword, turnstileToken)"));
  assert.ok(portal.includes("account.forgotPassword(submittedEmail, turnstileToken)"));
  assert.ok(widget.includes('action: "loom_auth"'));
  assert.ok(widget.includes('"expired-callback"'));
  assert.ok(widget.includes('"error-callback"'));
  assert.ok(widget.includes("turnstile.remove") || widget.includes("api.remove(widgetId)"));
  assert.ok(bridge.includes("turnstile_token: turnstileToken ||"));
  assert.ok(compose.includes("LOOM_TURNSTILE_SECRET_KEY"));
  assert.ok(!portal.includes("TURNSTILE_SECRET_KEY"), "Never include the secret in the rendered component");
});
