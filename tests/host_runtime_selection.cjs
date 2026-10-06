const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('../desktop-react/node_modules/typescript');
const root = path.resolve(__dirname, '..');
const embeddedRoot = path.resolve('test-embedded');
const dataRoot = path.resolve('test-profile');
const managedRoot = path.join(dataRoot, 'host-runtime', 'versions', '1.0.28');
const manifests = new Map();
const manifest = (version, publishedAt) => ({schema:1,version,protocol:1,platform:'win32',arch:'x64',publishedAt});
manifests.set(path.join(dataRoot,'host-runtime','current.json'), {schema:1,version:'1.0.28'});
const exportsObject = {};
const source = fs.readFileSync(path.join(root,'desktop-react/electron/hostRuntime.ts'),'utf8');
vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText, {
  exports: exportsObject,
  process: {platform:'win32',arch:'x64',resourcesPath:embeddedRoot,env:{},cwd:()=>root},
  require: name => {
    if(name === 'electron') return {app:{isPackaged:true,isReady:()=>true}};
    if(name === './hostPaths.js') return {hostDataPath:dataRoot};
    if(name === 'node:fs') return {
      existsSync: p => !missing.has(p),
      readFileSync: p => {if(!manifests.has(p)) throw Error('missing'); return JSON.stringify(manifests.get(p));},
    };
    return require(name);
  },
});
const missing = new Set();
const set = (embeddedDate, managedDate) => {
  manifests.set(path.join(embeddedRoot,'manifest.json'),manifest('1.0.0',embeddedDate));
  manifests.set(path.join(managedRoot,'manifest.json'),manifest('1.0.28',managedDate));
};
set('2026-10-06T07:57:22Z','2026-10-05T16:08:26Z');
assert.equal(exportsObject.currentHostRuntime().source,'embedded');
assert.equal(exportsObject.resolveHostBrowserExtensionRoot(root),path.join(embeddedRoot,'browser-current-tab'));
assert.equal(exportsObject.resolveHostPythonExecutable(root),path.join(embeddedRoot,'python.exe'));
set('2026-10-05T00:00:00Z','2026-10-06T00:00:00Z');
assert.equal(exportsObject.currentHostRuntime().source,'managed');
set('2026-10-06T00:00:00Z','2026-10-06T00:00:00Z');
assert.equal(exportsObject.currentHostRuntime().source,'managed');
set('invalid','2026-10-06T00:00:00Z');
assert.equal(exportsObject.currentHostRuntime().source,'managed');
set('2026-10-06T00:00:00Z','2026-10-05T00:00:00Z');
missing.add(path.join(embeddedRoot,'python.exe'));
assert.equal(exportsObject.currentHostRuntime().source,'managed');
missing.clear();
missing.add(path.join(managedRoot,'python.exe'));
assert.equal(exportsObject.currentHostRuntime().source,'embedded');
assert.equal(exportsObject.compareHostRuntimeBuilds({publishedAt:'invalid'},{publishedAt:'invalid'}),null);
console.log('Host runtime selection regression passed');
