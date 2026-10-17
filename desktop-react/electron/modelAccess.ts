import type { LoomModelPolicyAccess } from "./accountClient.js";
import type { ModelProfile, ModelSnapshot } from "./modelManager.js";
import { isManagedBuiltinSelection, modelPolicyGroup } from "./modelPolicySelection.js";

export function policyAllowsSelection(access: LoomModelPolicyAccess, selection: string): boolean {
  if (!isManagedBuiltinSelection(selection)) return true;
  if (!access.enabled) return false;
  const decision = access.decisions?.find(item => item.model_id === selection);
  if (decision) return decision.enabled === true;
  if (access.schema_version === 2) return false;
  if (!Array.isArray(access.decisions)) return access.models.includes(selection);
  return access.model_groups?.find(item => item.id === modelPolicyGroup(selection))?.enabled !== false;
}

export function policyBlockedMessage(access: LoomModelPolicyAccess, selection: string): string {
  const source = access.decisions?.find(item => item.model_id === selection)?.source;
  if (source === "catalog" || (!source && access.schema_version === 2)) return "Model is not in the confirmed provider catalog";
  const labels: Record<string, string> = { global_group: "model group", global: "global policy", account: "account access", user: "account rule", group: "access group" };
  return `Disabled by Loom Admin · ${labels[source || ""] || "model policy"}`;
}

/** Pure view projection; policy never rewrites transport metadata or catalog storage. */
export function applyModelPolicy(snapshot: ModelSnapshot, access: LoomModelPolicyAccess | null): ModelSnapshot {
  const project = <T extends ModelProfile>(profile: T): T => {
    const catalogAvailable = profile.catalogAvailable ?? profile.available !== false;
    const catalogMessage = profile.catalogMessage ?? (!profile.accessState ? profile.statusMessage : undefined);
    const base = { ...profile, catalogAvailable, catalogMessage, available: catalogAvailable, statusMessage: catalogMessage };
    if (profile.kind === "saved") return { ...base, accessState: "personal" };
    if (!access) return { ...base, accessState: "unknown" };
    const decision = access.decisions?.find(item => item.model_id === profile.selection);
    if (access.enabled && !decision && access.catalog?.providers?.some(provider =>
      provider.group_id === modelPolicyGroup(profile.selection) && provider.client_error === "invalid-provider-catalog")) {
      return { ...base, accessState: "unknown" };
    }
    const allowed = policyAllowsSelection(access, profile.selection);
    const notListed = decision?.source === "catalog" || (!decision && access.schema_version === 2);
    return { ...base, accessState: allowed ? "allowed" : notListed ? "not-listed" : "denied",
      available: allowed ? catalogAvailable : false,
      ...(!allowed ? { statusMessage: policyBlockedMessage(access, profile.selection) } : {}) };
  };
  const providers = (access?.catalog?.providers ?? []).map(provider => {
    const invalid = provider.client_error === "invalid-provider-catalog";
    const unavailable = !!provider.error || invalid;
    const state = provider.client_error === "unsupported-provider" ? "unsupported" as const
      : unavailable ? provider.last_success ? "stale" as const : "unavailable" as const : "synced" as const;
    return { groupId: provider.group_id, state, lastSuccess: provider.last_success, lastAttempt: provider.last_attempt,
      message: invalid ? "This provider's catalog could not be validated; its previous directory is retained."
        : provider.client_error === "unsupported-provider" ? "This provider requires a newer Host. Other providers continue syncing."
          : provider.error || undefined };
  });
  return { ...snapshot, catalogSync: { providers }, profiles: snapshot.profiles.map(project), primary: project(snapshot.primary),
    current: snapshot.current ? project(snapshot.current) : null };
}
