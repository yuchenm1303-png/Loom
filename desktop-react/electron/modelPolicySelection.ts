/** Only Loom-managed connections are subject to the central model policy.
 * Personal connections use profile: IDs, even when their model names match
 * built-in models. Keep this boundary identical for the picker and requests.
 */
export function isManagedBuiltinSelection(selection: string): boolean {
  const value = String(selection || "").trim();
  return value.startsWith("builtin:") || value.startsWith("managed:");
}

/** Connection identity comes from its selection, never its model name. */
export function modelPolicyGroup(selection: string): string | null {
  const value = String(selection || "").trim();
  for (const group of ["intern", "minimax", "deepseek", "ant-ling", "opencode-go", "opencode-console"]) {
    if ((!["intern", "opencode-go", "opencode-console"].includes(group) && value === `builtin:${group}`) || value.startsWith(`builtin:${group}:`)) return group;
  }
  if (value === "builtin:cqu" || value.startsWith("managed:")) return "managed-relay";
  return null;
}

/** New providers may appear in server catalogs before this Host supports them. */
export function catalogSelectionGroup(selection: string): string | null {
  return modelPolicyGroup(selection) ?? /^builtin:([a-z][a-z0-9-]*):.+$/.exec(String(selection || ""))?.[1] ?? null;
}

export function isPersonalSelection(selection: string): boolean {
  return /^(profile:|saved:).+/.test(String(selection || "").trim());
}
