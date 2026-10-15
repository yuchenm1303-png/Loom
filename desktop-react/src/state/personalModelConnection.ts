import type { ModelProfile } from "../types/loom";

/** Built-in launch URLs authenticate Loom accounts, never personal provider keys. */
export function personalConnectionTemplate(profile: ModelProfile) {
  if (profile.kind === "builtin") {
    return profile.personalConnection ?? { adapter: "openai-compatible" as const, baseUrl: "" };
  }
  const adapter = profile.adapter === "openai" ? "openai" as const : "openai-compatible" as const;
  return { adapter, baseUrl: adapter === "openai" ? "" : profile.baseUrl };
}
