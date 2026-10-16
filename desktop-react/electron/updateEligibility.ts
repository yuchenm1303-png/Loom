import { hostAccount } from "./hostAccount.js";
import { callHost, handleHostChannel, isHostProcess } from "./hostProcess.js";

/**
 * Only the Host owns the encrypted login session. Desktop children request
 * the Host's decision over authenticated local IPC; they must not inspect
 * their separate Chromium userData folder or trust a renderer-provided flag.
 */
const CHANNEL = "loom:update-eligibility";

let pendingHostEligibility: Promise<boolean> | null = null;
function hostUpdateEligibility(): Promise<boolean> {
  // Startup, installer, application and Host runtime checks may overlap.
  // Share only the in-flight request, never cache an authorization decision.
  if (pendingHostEligibility) return pendingHostEligibility;
  pendingHostEligibility = readHostUpdateEligibility().finally(() => { pendingHostEligibility = null; });
  return pendingHostEligibility;
}

async function readHostUpdateEligibility(): Promise<boolean> {
  try {
    let session = await hostAccount.status();
    // A single failed connection must not put all three update channels into
    // an error state. Retry only an outage, never a server denial or sign-out.
    // Keep sharing the whole operation, and still require a fresh server grant.
    if (!session.reachable) session = await hostAccount.status();
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
export async function mayReceiveUpdates(timeoutMs = 30000): Promise<boolean> {
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
