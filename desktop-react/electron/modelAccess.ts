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
    const allowed = policyAllowsSelection(access, profile.selection);
    const decision = access.decisions?.find(item => item.model_id === profile.selection);
    const notListed = decision?.source === "catalog" || (!decision && access.schema_version === 2);
    return { ...base, accessState: allowed ? "allowed" : notListed ? "not-listed" : "denied",
      available: allowed ? catalogAvailable : false,
      ...(!allowed ? { statusMessage: policyBlockedMessage(access, profile.selection) } : {}) };
  };
  return { ...snapshot, profiles: snapshot.profiles.map(project), primary: project(snapshot.primary),
    current: snapshot.current ? project(snapshot.current) : null };
}
