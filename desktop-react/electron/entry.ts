import { app } from "electron";
import { isHostProcess } from "./hostProcess.js";

// hostProcess selects a distinct UI data folder before acquiring either lock.
// A second desktop launch raises the UI; a second Host launch simply exits.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
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
