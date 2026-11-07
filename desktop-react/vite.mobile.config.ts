import { resolve } from "node:path";
import { defineConfig } from "vite";

// The phone app's page, bundled into the APK. Kept apart from the desktop/Web build
// so neither one can pick up the other's entry.
export default defineConfig({
  base: "./",
  build: {
    outDir: "dist-mobile",
    emptyOutDir: true,
    rollupOptions: { input: resolve(__dirname, "mobile.html") },
  },
});
