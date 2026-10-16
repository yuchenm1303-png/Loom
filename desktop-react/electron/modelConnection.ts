import type { ModelLaunchSpec } from "./modelManager.js";
import { isPersonalSelection, modelPolicyGroup } from "./modelPolicySelection.js";

export function builtinBaseUrl(selection: string): string | null {
  const group = modelPolicyGroup(selection);
  if (!group) return null;
  const root = new URL(process.env.LOOM_MODEL_GATEWAY_BASE_URL || "https://account.smirel.com/model/v1");
  if (root.username || root.password || root.search || root.hash
    || (root.protocol !== "https:" && !(root.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(root.hostname)))) {
    throw Error("Invalid Loom model gateway configuration");
  }
  return root.href.replace(/\/$/, "") + (group === "ant-ling" ? "" : `/${group}`);
}

export function assertModelConnection(spec: ModelLaunchSpec): void {
  if (isPersonalSelection(spec.selection)) {
    if (spec.kind !== "saved" || spec.authMode === "loom-account" || String(spec.apiKey || "").startsWith("loom_model_")) {
      throw Error("Personal model connection contains incompatible account authentication");
    }
    const root = builtinBaseUrl("builtin:ant-ling")!;
    const base = spec.baseUrl.replace(/\/$/, "");
    if (base === root || base.startsWith(root + "/") || base.startsWith("https://account.smirel.com/model/v1/")) {
      throw Error("Personal model connection cannot use a Loom account gateway");
    }
    return;
  }
  const expected = builtinBaseUrl(spec.selection);
  if (!expected || spec.kind !== "builtin" || spec.authMode !== "loom-account"
    || spec.baseUrl.replace(/\/$/, "") !== expected) {
    throw Error("Model connection identity is inconsistent. Refresh the model catalog or update the Host.");
  }
  if (spec.apiKey && !spec.apiKey.startsWith("loom_model_")) throw Error("A provider key cannot authenticate a Loom account connection");
}
