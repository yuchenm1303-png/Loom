import { applicationHash } from "../../dist-electron/applicationUpdateStore.js";
export function fixture(build = 1) {
  const contents = Object.fromEntries(["package.json", "dist/index.html", "dist/ui-baseline.json", "dist-electron/applicationEntry.js", "dist-electron/main.js", "dist-electron/preload.cjs", "dist-electron/hudPreload.cjs"].map(name => [name, Buffer.from(name + build)]));
  const release = { schema: 1, kind: "application", bootstrap: "0.1.82", build, sourceSha: "a".repeat(40), sourceTime: 4000000000,
    files: Object.entries(contents).map(([name, bytes]) => { const sha256 = applicationHash(bytes);
      return { path: name, size: bytes.length, sha256, url: `https://github.com/yuchenm1303-png/Loom/releases/download/app-${build}/${sha256}` }; }) };
  return { release, contents };
}
