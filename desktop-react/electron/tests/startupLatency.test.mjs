/**
 * Guards for what the Host does before the desktop can show conversations.
 *
 * Launch used to sit on "Starting Loom" for as long as the account service took
 * to answer: the Host refreshed the sign-in session and minted a model credential
 * (15 second timeouts each) before it would spawn the local runtime, after four
 * Python bridges had run one at a time on the main process. None of that needs to
 * happen first, and none of it is visible from a unit test of the pieces, so these
 * read `main.ts` and pin the ordering that matters.
 */

import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { test } from "node:test";

const main = await fs.readFile(new URL("../main.ts", import.meta.url), "utf8");

function between(source, start, end) {
  const from = source.indexOf(start);
  assert.notEqual(from, -1, `missing: ${start}`);
  const to = source.indexOf(end, from);
  assert.notEqual(to, -1, `missing: ${end}`);
  return source.slice(from, to);
}

const beforeSpawn = between(main, "private async startProcess()", "const child = spawn(python");

test("the runtime is spawned from local credentials, not after account round trips", () => {
  assert.match(beforeSpawn, /this\.account\.cachedCredentials\(\)/);
  for (const forbidden of [".modelCredential(", ".automationCredential(", ".authorizationCredentials(", ".refresh("]) {
    assert.ok(!beforeSpawn.includes(forbidden), `startProcess waits on the account service again through ${forbidden}`);
  }
});

test("the first model is chosen without blocking the main process", () => {
  assert.match(beforeSpawn, /await this\.models\.ensureInitialAsync\(\)/);
  assert.ok(!beforeSpawn.includes(".ensureInitial()"), "the blocking ensureInitial() would hold the Host for seconds");
});

test("missing credentials are delivered as soon as the runtime can take them", () => {
  const initialize = between(main, "private async initializeOnce()", "async connect()");
  const markedReady = initialize.indexOf("this.initialized = true");
  const delivered = initialize.indexOf("this.deliverCredentials()");
  assert.ok(markedReady !== -1 && delivered > markedReady, "deliverCredentials must run once the runtime is initialized");

  const deliver = between(main, "private deliverCredentials()", "get ready()");
  assert.match(deliver, /syncAccountToolAccessCredential\(\)/);
  assert.match(deliver, /this\.modelDeferred/);
  assert.match(deliver, /this\.ensureModelReady\(\)/);
});

test("asking for the model to be ready twice shares one run", () => {
  const ensure = between(main, "ensureModelReady(): Promise<void>", "private deliverCredentials()");
  assert.match(ensure, /this\.modelReadyPromise \?\?|if \(!this\.modelReadyPromise\)/);
  assert.match(ensure, /finally\(\(\) => \{ this\.modelReadyPromise = null; \}\)/);
});

test("a desktop window connecting starts the runtime before its first request arrives", () => {
  const hostBranch = between(main, "onDesktopClientsChanged((pids)", "await startHostTransport()");
  assert.match(hostBranch, /pids\.length > 0/);
  assert.match(hostBranch, /rpc\.connect\(\)/);
});
