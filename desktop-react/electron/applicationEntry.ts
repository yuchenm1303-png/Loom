import { app } from "electron";
import { isHostProcess } from "./hostProcess.js";

// The installed entry acquired the process lock. All modules stay in this release.
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
  await import("./webRelayAuth.js");
  await import("./main.js");
}
