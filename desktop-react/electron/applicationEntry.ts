import { app } from "electron";
import { isHostProcess } from "./hostProcess.js";

// The stable entry acquired the process lock and selected this verified bundle.
// All dynamic imports below stay within this application release.
{
  if (!isHostProcess) {
    const callbackFromArgs = (args: string[]) =>
      args.find((arg) => arg.startsWith("loom://auth/callback?")) || "";
    app.on("second-instance", (_event, argv) => {
      void import("./main.js").then(({ showDesktopWindow, handleDesktopOAuthUrl }) => {
        showDesktopWindow();
        const callback = callbackFromArgs(argv);
        if (callback) void handleDesktopOAuthUrl(callback);
      });
    });
    app.on("open-url", (event, url) => {
      event.preventDefault();
      void import("./main.js").then(({ showDesktopWindow, handleDesktopOAuthUrl }) => {
        showDesktopWindow();
        void handleDesktopOAuthUrl(url);
      });
    });
  }
  await import("./updater.js");
  await import("./applicationUpdater.js");
  await import("./webRelayAuth.js");
  await import("./main.js");
}
