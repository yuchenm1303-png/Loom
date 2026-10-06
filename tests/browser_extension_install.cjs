const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const ts = require('../desktop-react/node_modules/typescript');

(async () => {
  const sandbox = await fs.mkdtemp(path.join(os.tmpdir(), 'loom-extension-install-'));
  try {
    const source = path.join(sandbox,'source'), target = path.join(sandbox,'install');
    await fs.mkdir(path.join(source,'nested'),{recursive:true});
    await fs.writeFile(path.join(source,'manifest.json'),' {"version":"0.1.22"}');
    await fs.writeFile(path.join(source,'nested','worker.js'),'native-v22');
    const code = await fs.readFile(path.join(__dirname,'../desktop-react/electron/browserExtensionAssets.ts'),'utf8');
    const exportsObject = {};
    vm.runInNewContext(ts.transpileModule(code,{compilerOptions:{module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText,
      {exports:exportsObject,require});
    const sync = exportsObject.syncExtensionInstall;
    assert.equal(await sync(source,target,'paired-test-config','signal-1'),true);
    assert.equal(await fs.readFile(path.join(target,'nested','worker.js'),'utf8'),'native-v22');
    assert.equal(await sync(source,target,'paired-test-config','signal-2'),false);
    assert.equal(await fs.readFile(path.join(target,'extension-update.json'),'utf8'),'signal-1');
    await fs.writeFile(path.join(target,'nested','worker.js'),'stale-v21');
    assert.equal(await sync(source,target,'paired-test-config','signal-3'),true);
    assert.equal(await fs.readFile(path.join(target,'nested','worker.js'),'utf8'),'native-v22');
    assert.equal(await fs.readFile(path.join(target,'bridge-config.json'),'utf8'),'paired-test-config');
    assert.equal(await fs.readFile(path.join(target,'extension-update.json'),'utf8'),'signal-3');
    assert.equal(await sync(source,target,'repaired-test-config','signal-4'),true);
    const {hostRuntimeIdentity} = await import('../desktop-react/scripts/host-runtime-identity.mjs');
    const identity = hostRuntimeIdentity(path.resolve(__dirname,'..'),{});
    assert.match(identity.version,/^1\.0\.\d{10}$/);
    assert.match(identity.sourceSha,/^[a-f0-9]{40}$/);
    assert.equal(hostRuntimeIdentity(path.resolve(__dirname,'..'),{}).version,identity.version);
    assert.throws(()=>hostRuntimeIdentity(path.resolve(__dirname,'..'),{LOOM_HOST_RUNTIME_VERSION:'invalid'}));
    console.log('Extension installation and release identity regression passed');
  } finally { await fs.rm(sandbox,{recursive:true,force:true}); }
})().catch(error => { console.error(error); process.exitCode=1; });
