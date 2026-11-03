// Separate native IA32 Loom Desktop + local Host package. Do not modify the
// production x64 builder settings, app identity, or updater feed.
const base = require("../package.json").build;
module.exports = {
  ...base,
  appId: "com.loom.agent.ia32",
  productName: "Loom x86",
  executableName: "Loom",
  // Electron 44+ do not publish Windows ia32. Pin the x86 path to the
  // latest security-maintained Electron 43 patch without changing x64.
  // This must not affect the regular x64 release's Electron version.
  electronVersion: "43.8.6",
  artifactName: "Loom-Setup-x86-${version}.${ext}",
  directories: { ...base.directories, output: "release-x86" },
  extraResources: [
    { from: "build/icon.png", to: "loom-icon.png" },
    { from: "runtime-dist-x86/python", to: "." },
    { from: "../extensions/browser-current-tab", to: "browser-current-tab" },
  ],
  win: {
    ...base.win,
    target: [{ target: "nsis", arch: ["ia32"] }],
    requestedExecutionLevel: "asInvoker",
  },
  nsis: {
    ...base.nsis,
    include: "build/installer-x86.nsh",
    oneClick: true,
    runAfterFinish: true,
  },
  // autoUpdater is disabled in IA32 at runtime (separate x86 channel pending).
  // Publishing release metadata to x64 update feeds is forbidden.
  publish: null,
  afterPack: undefined,
};
