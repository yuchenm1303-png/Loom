import assert from "node:assert/strict";
import { test } from "node:test";
import { authorizationSync } from "../../dist-electron/authorizationSync.js";

const tick = () => new Promise(resolve => setImmediate(resolve));

test("automation delivery does not wait for model minting; rotation is republished", async () => {
  let release;
  let access = "access-1";
  const delivered = [];
  const sync = authorizationSync({ epoch: () => "a", ready: () => true,
    automation: async () => access,
    model: () => new Promise(resolve => { release = () => { access = "access-2"; resolve("model-1"); }; }),
    deliver: async value => { delivered.push(value); },
  });
  const pending = sync();
  await tick();
  assert.deepEqual(delivered, [{credential: "access-1"}]);
  release(); await pending; await tick();
  assert(delivered.some(value => value.credential === "access-2"));
  assert(delivered.some(value => value.modelCredential === "model-1"));
  assert(delivered.some(value => value.modelCredential === "model-1" && value.credential === "access-2"));
});

test("identity change discards both old credential lanes, even A -> B -> A", async () => {
  let epoch = 1;
  const readers = [];
  const delivered = [];
  const sync = authorizationSync({ epoch: () => String(epoch), ready: () => true,
    automation: () => new Promise(resolve => readers.push(resolve)),
    model: () => new Promise(resolve => readers.push(resolve)),
    deliver: async value => { delivered.push(value); },
  });
  const pending = sync(); await tick();
  epoch += 2;
  readers[0]("old-access"); readers[1]("old-model");
  await tick();
  // Model completion asks the automation lane to read the current identity.
  readers[2]("current-access");
  await pending; await tick();
  assert.deepEqual(delivered, [{credential: "current-access"}]);
});

test("network failure does not sign out the other lane; explicit logout clears both", async () => {
  let loggedOut = false;
  let epoch = 1;
  const delivered = [];
  const sync = authorizationSync({epoch: () => String(epoch), ready: () => true,
    automation: async () => loggedOut ? "" : "valid-access",
    model: async () => { if (!loggedOut) throw Error("network"); return ""; },
    deliver: async value => { delivered.push(value); },
  });
  await sync(); await tick();
  assert(delivered.some(value => value.credential === "valid-access"));
  assert(!delivered.some(value => "modelCredential" in value));
  loggedOut = true; epoch++; await sync(); await tick();
  assert(delivered.some(value => value.credential === ""));
  assert(delivered.some(value => value.modelCredential === ""));
});

test("401 recovery forces both credential lanes and retries after a transient failure", async () => {
  const forced = [];
  let fail = true;
  const sync = authorizationSync({epoch: () => "a", ready: () => true,
    automation: async force => { forced.push(['automation', force]); if (force && fail) throw Error('offline'); return 'a'; },
    model: async force => { forced.push(['model', force]); return 'm'; },
    deliver: async () => {},
  });
  await sync(true); await tick();
  fail = false; await sync(); await tick();
  assert(forced.filter(([name, force]) => name === 'automation' && force).length >= 2);
  assert(forced.some(([name, force]) => name === 'model' && force));
});

test("account transition does not read or republish the previous account", async () => {
  let ready = false;
  const calls = [];
  const sync = authorizationSync({epoch: () => 'switching', ready: () => ready,
    automation: async () => { calls.push('read-account'); return 'new-account'; },
    model: async () => { calls.push('read-model'); return 'new-model'; },
    deliver: async () => { calls.push('deliver'); },
  });
  await sync(true);
  assert.deepEqual(calls, []);
  ready = true; await sync(); await tick();
  assert(calls.includes('deliver'));
});

test("wire messages keep automation enabled on an older Host and explicitly clear both on logout", async () => {
  let signedIn = true;
  let account = '', model = '';
  const sync = authorizationSync({epoch: () => String(signedIn), ready: () => true,
    automation: async () => signedIn ? 'account-token' : '',
    model: async () => signedIn ? 'model-token' : '',
    deliver: async value => {
      // Old Host semantics: an absent account credential means empty.
      account = value.credential || '';
      if ('modelCredential' in value) model = value.modelCredential;
    },
  });
  await sync(); await tick();
  assert.equal(account, 'account-token');
  assert.equal(model, 'model-token');
  signedIn = false; await sync(); await tick();
  assert.equal(account, ''); assert.equal(model, '');
});
