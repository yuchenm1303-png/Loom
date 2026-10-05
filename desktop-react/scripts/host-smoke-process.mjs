// A smoke client calls Host IPC, whose request deadline is 120 seconds. Allow
// Electron startup as well; a shorter watchdog would kill a valid cold start.
export const CLIENT_TIMEOUT_MS = 150_000;

export function waitForSmokeProcess(child, { label, timeoutMs = CLIENT_TIMEOUT_MS } = {}) {
  return new Promise((resolve, reject) => {
    let output = "";
    const capture = (chunk) => { output = (output + chunk).slice(-16_000); };
    child.stdout?.on("data", capture);
    child.stderr?.on("data", capture);
    const finish = (error) => {
      clearTimeout(timer);
      child.removeListener("error", onError);
      child.removeListener("close", onClose);
      child.stdout?.removeListener("data", capture);
      child.stderr?.removeListener("data", capture);
      if (error) reject(error);
      else resolve(output);
    };
    const onError = (error) => finish(new Error(`${label}: ${error.message}\n${output}`, { cause: error }));
    const onClose = (code, signal) => finish(code === 0 ? null
      : new Error(`${label} exited (code=${code}, signal=${signal ?? "none"})\n${output}`));
    // The caller owns tree cleanup in its finally block. Reject on the deadline
    // itself instead of killing the parent and mistaking a null exit for a crash.
    const timer = setTimeout(() => finish(new Error(`${label} timed out after ${timeoutMs}ms\n${output}`)), timeoutMs);
    child.once("error", onError);
    child.once("close", onClose);
  });
}
