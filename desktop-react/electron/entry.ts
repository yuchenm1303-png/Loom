import { app } from "electron";
import { isHostProcess } from "./hostRuntime.js";

// hostRuntime selects a distinct UI data folder before acquiring either lock.
// A second desktop launch raises the UI; a second Host launch simply exits.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  if (!isHostProcess) app.on("second-instance", () => {
    void import("./main.js").then(({ showDesktopWindow }) => showDesktopWindow());
  });
  await import("./updater.js");
  await import("./webRelayAuth.js");
  await import("./main.js");
}
