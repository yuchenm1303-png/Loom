import type { ModelProfile } from "../types/loom";

// Saved credentials belong to individual profiles. Group only identical
// endpoints and adapters; never infer a shared credential from a hostname.
export function groupModelProfiles(profiles: ModelProfile[]) {
  const groups = new Map<string, { id: string; name: string; endpoint: string; profiles: ModelProfile[] }>();
  for (const profile of profiles) {
    const endpoint = profile.baseUrl.trim().replace(/\/+$/, "");
    const id = profile.kind === "saved"
      ? `saved:${profile.adapter}:${endpoint}`
      : profile.groupId || `builtin:${profile.adapter}:${endpoint}`;
    let host = endpoint || "api.openai.com";
    try { host = new URL(endpoint).host; } catch { /* Keep local endpoint labels readable. */ }
    if (!groups.has(id)) groups.set(id, {
      id,
      name: profile.kind === "saved" ? host : profile.groupName || host,
      endpoint: host,
      profiles: [],
    });
    groups.get(id)!.profiles.push(profile);
  }
  return [...groups.values()];
}
