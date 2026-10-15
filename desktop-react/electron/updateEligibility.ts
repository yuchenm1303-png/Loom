import { hostAccount } from "./hostAccount.js";

/**
 * Update delivery is an account-scoped server decision, not a renderer
 * preference. Re-read /auth/me at each check and again at activation so an
 * administrator can revoke access while a release is being prepared.
 *
 * Do not fail open on network outages or after sign-out: a stale session must
 * never turn an admin-disabled account into an eligible updater.
 */
export async function mayReceiveUpdates(): Promise<boolean> {
  try {
    const session = await hostAccount.status();
    return session.reachable === true
      && session.authenticated === true
      && session.user?.status === "active"
      // Older account services may not have deployed this field yet.
      && session.user.updates_enabled !== false;
  } catch {
    return false;
  }
}
