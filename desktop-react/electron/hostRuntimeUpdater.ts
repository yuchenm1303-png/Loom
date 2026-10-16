import { app } from "electron";
import { currentHostRuntime } from "./hostRuntime.js";

// Compatibility response for existing Web clients. Runtime upgrades now use
// the same complete installer as the main process and UI.
export interface HostRuntimeUpdateState {
  enabled: boolean;
  phase: "disabled" | "incompatible";
  currentVersion: string;
  currentProtocol: number;
  requiredProtocol?: number;
  requiredBootstrapVersion?: string;
  error?: string;
}

export function hostRuntimeUpdateState(): HostRuntimeUpdateState {
  const runtime = currentHostRuntime();
  return { enabled: false, phase: "disabled", currentVersion: runtime.version, currentProtocol: runtime.protocol };
}

export async function ensureHostRuntimeUpdate(requiredProtocol = 0): Promise<HostRuntimeUpdateState> {
  const { ensureBootstrapUpdate } = await import("./updater.js");
  await ensureBootstrapUpdate();
  const state = hostRuntimeUpdateState();
  return state.currentProtocol < requiredProtocol
    ? { ...state, phase: "incompatible", requiredProtocol, requiredBootstrapVersion: app.getVersion(),
        error: "Install the latest complete Loom release to update the local runtime." }
    : state;
}
