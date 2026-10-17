import type { LoomModelPolicyAccess } from "./accountClient.js";
import { catalogSelectionGroup, modelPolicyGroup } from "./modelPolicySelection.js";

type Catalog = NonNullable<LoomModelPolicyAccess["catalog"]>;
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);

/** Validate/commit each provider independently. Transport/auth envelopes remain atomic. */
export function reconcileModelCatalog(access: LoomModelPolicyAccess, previous: LoomModelPolicyAccess | null): LoomModelPolicyAccess {
  if (access.schema_version !== 2) return access;
  const raw = access.catalog;
  if (!raw || !Array.isArray(raw.models) || !raw.models.length || raw.models.length > 20_000 || !Array.isArray(access.decisions)) {
    throw Error("Model policy returned an invalid catalog envelope");
  }
  const groups = new Set<string>();
  const invalid = new Set<string>();
  const models = new Map<string, Catalog["models"]>();
  const decisions = new Map<string, NonNullable<LoomModelPolicyAccess["decisions"]>>();
  const modelIds = new Set<string>();
  const decisionIds = new Set<string>();
  const statuses = new Map<string, Catalog["providers"][number]>();
  for (const row of previous?.catalog?.models ?? []) groups.add(row.group_id);
  for (const status of Array.isArray(raw.providers) ? raw.providers : []) {
    if (!object(status) || typeof status.group_id !== "string") continue;
    groups.add(status.group_id);
    statuses.set(status.group_id, { group_id: status.group_id,
      last_success: typeof status.last_success === "number" ? status.last_success : null,
      last_attempt: typeof status.last_attempt === "number" ? status.last_attempt : null,
      error: typeof status.error === "string" && status.error ? "Provider discovery failed; last successful catalog retained." : "" });
  }
  for (const row of raw.models) {
    if (!object(row)) continue;
    const inferred = typeof row.model_id === "string" ? catalogSelectionGroup(row.model_id) : null;
    const group = typeof row.group_id === "string" ? row.group_id : inferred;
    if (!group) continue;
    groups.add(group);
    const list = models.get(group) ?? [];
    if (inferred !== group || typeof row.model !== "string" || !row.model.trim()
      || typeof row.available !== "boolean" || typeof row.name !== "string"
      || typeof row.group_name !== "string" || modelIds.has(row.model_id as string)) {
      invalid.add(group);
      if (inferred) { groups.add(inferred); invalid.add(inferred); }
      continue;
    }
    list.push(row as Catalog["models"][number]);
    modelIds.add(row.model_id as string);
    models.set(group, list);
  }
  for (const decision of access.decisions) {
    if (!object(decision) || typeof decision.model_id !== "string") continue;
    const group = catalogSelectionGroup(decision.model_id);
    if (!group) continue;
    groups.add(group);
    const list = decisions.get(group) ?? [];
    if (typeof decision.enabled !== "boolean" || typeof decision.source !== "string"
      || decisionIds.has(decision.model_id)) invalid.add(group);
    else list.push(decision as NonNullable<LoomModelPolicyAccess["decisions"]>[number]);
    decisionIds.add(decision.model_id);
    decisions.set(group, list);
  }
  const resultModels: Catalog["models"] = [], resultDecisions: NonNullable<LoomModelPolicyAccess["decisions"]> = [];
  const providers: Catalog["providers"] = [];
  for (const group of groups) {
    const rows = models.get(group) ?? [], rules = decisions.get(group) ?? [];
    const ruleIds = new Set(rules.map(rule => rule.model_id));
    const status = statuses.get(group) ?? { group_id: group, last_success: null, last_attempt: null, error: "" };
    const supported = modelPolicyGroup(group === "managed-relay" ? "managed:probe" : `builtin:${group}:probe`) === group;
    if (!supported) {
      providers.push({ ...status, client_error: "unsupported-provider" });
      continue;
    }
    if (!rows.length || invalid.has(group) || rows.some(row => !ruleIds.has(row.model_id))) {
      resultModels.push(...(previous?.catalog?.models.filter(row => row.group_id === group) ?? []));
      resultDecisions.push(...(previous?.decisions?.filter(rule => catalogSelectionGroup(rule.model_id) === group) ?? []));
      const old = previous?.catalog?.providers?.find(item => item.group_id === group);
      providers.push({ ...status, last_success: old?.last_success ?? null, client_error: "invalid-provider-catalog" });
      continue;
    }
    resultModels.push(...rows);
    resultDecisions.push(...rules);
    providers.push(status);
  }
  return { ...access, models: resultDecisions.filter(rule => rule.enabled).map(rule => rule.model_id), decisions: resultDecisions,
    catalog: { ...raw, models: resultModels, providers } };
}
