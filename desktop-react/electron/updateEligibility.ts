import { hostAccount } from "./hostAccount.js";

/**
 * Update delivery is an account-scoped server decision, not a renderer
 * preference. Re-read /auth/me at each check and again at activation so an
 * administrator can revoke access while a release is being prepared.
 *
 * Do not fail open on network outages or after sign-out: a stale session must
 * never turn an admin-disabled account into an eligible updater.
 */
export async function mayReceiveUpdates(timeoutMs = 3000): Promise<boolean> {
  // Startup must not block behind a slow account endpoint. A timed-out
  // verification denies update delivery, never startup or normal Agent work.
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    const session = await Promise.race([
      hostAccount.status(),
      new Promise<never>((_resolve, reject) => {
        timeout = setTimeout(() => reject(new Error("Update eligibility timed out")), timeoutMs);
        timeout.unref?.();
      }),
    ]);
    return session.reachable === true
      && session.authenticated === true
      && session.user?.status === "active"
      // Older account services may not have deployed this field yet.
      && session.user.updates_enabled !== false;
  } catch {
    return false;
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}
