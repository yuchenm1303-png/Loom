import { hostAccount } from "./hostAccount.js";
import { callHost, handleHostChannel, isHostProcess } from "./hostProcess.js";

/**
 * Only the Host owns the encrypted login session. Desktop children request
 * the Host's decision over authenticated local IPC; they must not inspect
 * their separate Chromium userData folder or trust a renderer-provided flag.
 */
const CHANNEL = "loom:update-eligibility";

async function hostUpdateEligibility(): Promise<boolean> {
  try {
    const session = await hostAccount.status();
    return session.reachable === true
      && session.authenticated === true
      && session.user?.status === "active"
      // During independent rollout older account APIs do not yet expose this
      // field; once deployed, false revokes all update channels.
      && session.user.updates_enabled !== false;
  } catch {
    return false;
  }
}
handleHostChannel(CHANNEL, () => hostUpdateEligibility());

/** Fail closed on outages, sign-out, or explicit admin revocation. */
export async function mayReceiveUpdates(timeoutMs = 3000): Promise<boolean> {
  // A slow account API is not allowed to block normal Agent startup.
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    const pending = isHostProcess ? hostUpdateEligibility() : callHost(CHANNEL);
    const allowed = await Promise.race([
      pending,
      new Promise<false>(resolve => {
        timeout = setTimeout(() => resolve(false), timeoutMs);
        timeout.unref?.();
      }),
    ]);
    return allowed === true;
  } catch {
    return false;
  } finally {
    if (timeout) clearTimeout(timeout);
  }
}
