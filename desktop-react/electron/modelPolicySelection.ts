/** Only Loom-managed connections are subject to the central model policy.
 * Personal connections use profile: IDs, even when their model names match
 * built-in models. Keep this boundary identical for the picker and requests.
 */
export function isManagedBuiltinSelection(selection: string): boolean {
  const value = String(selection || "").trim();
  return value.startsWith("builtin:") || value.startsWith("managed:");
}
