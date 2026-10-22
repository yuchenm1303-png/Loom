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

test("a landing-page request becomes a reviewable composer draft", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const composer = readFileSync(new URL("../../src/components/Composer.tsx", import.meta.url), "utf8");
  const lens = readFileSync(new URL("../../src/components/PortalLiquidCursor.tsx", import.meta.url), "utf8");
  assert.ok(portal.includes('sessionStorage.setItem("loom.portal.pendingPrompt"'));
  assert.ok(portal.includes('pendingPrompt && authenticated && hostState === "online"'));
  assert.ok(composer.includes('sessionStorage.getItem("loom.portal.pendingPrompt")'));
  assert.ok(composer.includes("setDrafts("));
  assert.ok(lens.includes('root.classList.contains("loom-editorial")'));
});


test("magnetic glass refraction is smooth on short and tall targets in both homepage bundles", () => {
  const react = readFileSync(new URL("../../src/components/PortalLiquidCursor.tsx", import.meta.url), "utf8");
  const standalone = readFileSync(new URL("../../../artifacts/loom-site/assets/cursor.js", import.meta.url), "utf8");
  for (const source of [react, standalone]) {
    assert.ok(source.includes("vec2 bendReach = max(vec2(22.0, 13.0), halfSize * vec2(0.68, 0.66))"));
    assert.ok(source.includes("refraction *= inversesqrt(vec2(1.0) + bendRatio * bendRatio)"));
    assert.ok(!source.includes("refraction.y = clamp("), "a hard Y clamp flattens the optics into horizontal cutoff bands");
    assert.ok(source.includes("float mask = 1.0 - smoothstep("), "keep the rounded glass silhouette");
  }

  const smoothLimit = (value, reach) => value / Math.sqrt(1 + (value / reach) ** 2);
  for (const buttonHeight of [28, 36, 44, 60, 120]) {
    const reach = Math.max(13, buttonHeight / 2 * 0.66);
    const samples = [-100, -40, -20, -10, -1, 0, 1, 10, 20, 40, 100]
      .map(value => smoothLimit(value, reach));
    assert.ok(samples.every(value => Number.isFinite(value) && Math.abs(value) < reach));
    assert.ok(samples.every((value, index) => index === 0 || samples[index - 1] < value),
      "optical displacement must change smoothly without a flat clipped band");
    const oldHardCutoff = buttonHeight / 2 * .32;
    const epsilon = .001;
    const leftSlope = (smoothLimit(oldHardCutoff, reach) - smoothLimit(oldHardCutoff - epsilon, reach)) / epsilon;
    const rightSlope = (smoothLimit(oldHardCutoff + epsilon, reach) - smoothLimit(oldHardCutoff, reach)) / epsilon;
    assert.ok(Math.abs(leftSlope - rightSlope) < .001,
      "the old cutoff location must no longer produce a visible slope break");
  }
});


test("editorial auth uses a centered minimal dialog while the classic portal is untouched", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../../src/components/portal-auth-modal.css", import.meta.url), "utf8");
  assert.ok(portal.includes('import "./portal-auth-modal.css"'));
  assert.ok(portal.includes("editorial-auth-dialog"));
  assert.ok(portal.includes("editorial-modal-brand"));
  assert.ok(portal.includes('aria-modal={editorial && drawerOpen ? true : undefined}'));
  assert.ok(portal.includes('inert={drawerOpen}'));
  assert.ok(portal.includes('setPageMode(next)'));
  assert.match(css, /place-items:\s*center/);
  assert.match(css, /width:\s*min\(476px,/);
  assert.match(css, /max-height:\s*calc\(100dvh - 36px\)/);
  assert.match(css, /\.loom-oauth-grid/);
  assert.match(css, /\.loom-account-form/);
  assert.match(css, /\.loom-host-refined/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.ok(!css.includes(".loom-classic "));
});

test("editorial sign-in preserves shared OAuth, registration, recovery, and modal keyboard controls", () => {
  const portal = readFileSync(new URL("../../src/components/WebPortal.tsx", import.meta.url), "utf8");
  for (const flow of ["account.login(", "account.oauthExchange(", "account.registerStart(",
    "account.verifyEmail(", "account.resendEmail(", "account.forgotPassword(",
    "account.resetPassword(", "<HostSetupActions", "onOpenProfile", "resetAuthFlow("]) {
    assert.ok(portal.includes(flow), "Shared authentication flow missing: " + flow);
  }
  for (const action of ['event.key === "Escape"', 'event.key !== "Tab"',
    "modalReturnFocusRef", "modalRef.current", "openEditorialModal()", "dismissDrawer()"]) {
    assert.ok(portal.includes(action), "Keyboard or focus handling missing: " + action);
  }
});
