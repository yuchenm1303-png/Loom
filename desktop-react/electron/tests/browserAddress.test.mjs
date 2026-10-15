import assert from "node:assert/strict";
import test from "node:test";
import { normalizeBrowserAddress } from "../../dist-electron/browserAddress.js";

test("normal URLs and local development addresses", () => {
  assert.equal(normalizeBrowserAddress("https://example.com/path?q=1"), "https://example.com/path?q=1");
  assert.equal(normalizeBrowserAddress("example.com"), "https://example.com/");
  assert.equal(normalizeBrowserAddress("example.com:8443"), "https://example.com:8443/");
  assert.equal(normalizeBrowserAddress("localhost:3000"), "http://localhost:3000/");
  assert.equal(normalizeBrowserAddress("127.0.0.1:8080"), "http://127.0.0.1:8080/");
});

test("ordinary words become a search query", () => {
  assert.equal(normalizeBrowserAddress("electric power price"), "https://www.google.com/search?q=electric%20power%20price");
});

test("no renderer IPC command can navigate to privileged local protocols", () => {
  for (const url of [
    "javascript:alert(1)", "file:///C:/Windows", "data:text/html,hello",
    "chrome://settings", "about:blank", "devtools://devtools", "ftp://example.org",
    "https://user:password@example.com/",
  ]) {
    assert.throws(() => normalizeBrowserAddress(url), url);
  }
  assert.throws(() => normalizeBrowserAddress(""), /address/);
  assert.throws(() => normalizeBrowserAddress("a".repeat(2049)), /2048/);
});
