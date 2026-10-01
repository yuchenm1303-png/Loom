import { createRequire } from "node:module";
import { defineConfig, type PluginOption } from "vite";

// React/TSX compilation is provided by Vite's esbuild pipeline. The React plugin
// adds Fast Refresh in development, but it must not make production builds or
// CI packaging fail if npm leaves this optional developer convenience out of a
// partially reified node_modules tree.
const require = createRequire(import.meta.url);
const plugins: PluginOption[] = [];
try {
  const loaded = require("@vitejs/plugin-react") as { default?: () => PluginOption } | (() => PluginOption);
  const react = typeof loaded === "function" ? loaded : loaded.default;
  if (react) plugins.push(react());
} catch {
  // Build remains fully functional; only React Fast Refresh is unavailable.
}

export default defineConfig({
  plugins,
  base: "./",
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
