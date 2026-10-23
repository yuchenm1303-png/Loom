import { build } from "esbuild";
import { fileURLToPath } from "node:url";

await build({
  absWorkingDir: fileURLToPath(new URL("../", import.meta.url)),
  entryPoints: ["src/portalCursorStandalone.tsx"],
  outfile: "../artifacts/loom-site/assets/cursor.js",
  bundle: true,
  minify: true,
  format: "iife",
  target: ["chrome110", "safari16.4"],
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"production"' },
});
