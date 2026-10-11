// Runs in Node's loader worker. Resolve installed dependencies from the stable
// package, rather than searching next to the downloaded application code.
let installedParent = "";
export function initialize(data: { parentURL: string }) { installedParent = data.parentURL; }
export async function resolve(specifier: string, context: { parentURL?: string },
  nextResolve: (specifier: string, context: object) => Promise<unknown>) {
  const bare = !specifier.startsWith(".") && !specifier.startsWith("/") && !specifier.includes(":");
  return nextResolve(specifier, bare ? { ...context, parentURL: installedParent } : context);
}
