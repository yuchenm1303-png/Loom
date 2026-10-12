/** Optional bridge extensions must not become startup requirements.
 * Older Hosts advertise their command set in the usage error. Treat only a
 * missing command in that declaration as unsupported; operational failures
 * must still reach the caller.
 */
export async function invokeOptionalBridgeOperation<T>(
  script: string,
  command: string,
  invoke: () => Promise<T>,
): Promise<{ supported: false } | { supported: true; value: T }> {
  try {
    return { supported: true, value: await invoke() };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const escaped = script.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const advertised = message.match(new RegExp(`usage:\\s*${escaped}\\s*\\{([^}]+)\\}`));
    if (advertised && !advertised[1].split("|").map(value => value.trim()).includes(command)) {
      return { supported: false };
    }
    throw error;
  }
}
