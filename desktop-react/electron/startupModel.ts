import type { ModelLaunchSpec } from "./modelManager.js";

/** Model authorization must not prevent local services from opening. */
export async function startupModel(
  selected: ModelLaunchSpec, credential: () => Promise<string>,
): Promise<{ spec: ModelLaunchSpec; deferred: boolean }> {
  if (selected.authMode !== "loom-account") return { spec: selected, deferred: false };
  try {
    const apiKey = await credential();
    if (!apiKey) throw new Error("Account authorization is pending");
    return { spec: { ...selected, apiKey }, deferred: false };
  } catch {
    return { spec: { ...selected, apiKey: "" }, deferred: true };
  }
}
