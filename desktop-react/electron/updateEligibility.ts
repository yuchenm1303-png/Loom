import { hostAccount } from "./hostAccount.js";
import { callHost, handleHostChannel, isHostProcess } from "./hostProcess.js";
import { recordUpdateAccessDiagnostic } from "./updateAccessDiagnostics.js";

export interface UpdateAccessDecision {
  allowed: boolean;
  reason: "allowed" | "unconfigured" | "unreachable" | "signed-out" | "inactive" | "revoked" | "timeout" | "verification-error";
  code?: string;
  status?: number;
}
const CHANNEL = "loom:update-eligibility";
const DETAIL_CHANNEL = "loom:update-access";
let pending: Promise<UpdateAccessDecision> | null = null;
let lastFailed = false;

function hostUpdateAccess(): Promise<UpdateAccessDecision> {
  // Share work only while it is running. Never reuse a grant for activation.
  if (pending) return pending;
  const started = Date.now();
  pending = readHostUpdateAccess().then(decision => {
    if (!decision.allowed || lastFailed) recordUpdateAccessDiagnostic({
      reason: decision.reason, code: decision.code, status: decision.status, elapsedMs: Date.now() - started,
    });
    lastFailed = !decision.allowed;
    return decision;
  }).finally(() => { pending = null; });
  return pending;
}
async function readHostUpdateAccess(): Promise<UpdateAccessDecision> {
  try {
    let session = await hostAccount.status();
    // Retry transport/service outages, never a reachable server denial.
    if (!session.reachable && session.configured !== false) session = await hostAccount.status();
    if (session.configured === false) return { allowed: false, reason: "unconfigured" };
    if (!session.reachable) return { allowed: false, reason: "unreachable", ...session.verificationFailure };
    if (!session.authenticated || !session.user) return { allowed: false, reason: "signed-out" };
    if (session.user.status !== "active") return { allowed: false, reason: "inactive" };
    if (session.user.updates_enabled === false) return { allowed: false, reason: "revoked" };
    return { allowed: true, reason: "allowed" };
  } catch {
    return { allowed: false, reason: "verification-error" };
  }
}
// Keep the boolean IPC contract for existing clients.
handleHostChannel(CHANNEL, async () => (await hostUpdateAccess()).allowed);
handleHostChannel(DETAIL_CHANNEL, () => hostUpdateAccess());

export async function verifyUpdateAccess(timeoutMs = 35000): Promise<UpdateAccessDecision> {
  // Allow two ordinary 15s service attempts, but bound refresh/retry chains too.
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const decision = await Promise.race([
      isHostProcess ? hostUpdateAccess() : callHost(DETAIL_CHANNEL),
      new Promise<UpdateAccessDecision>(resolve => {
        timer = setTimeout(() => {
          if (isHostProcess) recordUpdateAccessDiagnostic({ reason: "timeout", elapsedMs: timeoutMs });
          resolve({ allowed: false, reason: "timeout" });
        }, timeoutMs);
        timer.unref?.();
      }),
    ]) as UpdateAccessDecision;
    if (!decision || typeof decision.allowed !== "boolean" || typeof decision.reason !== "string") {
      return { allowed: false, reason: "verification-error" };
    }
    return decision;
  } catch {
    return { allowed: false, reason: "verification-error" };
  } finally {
    if (timer) clearTimeout(timer);
  }
}
/** Fail closed on outages, sign-out, or explicit admin revocation. */
export async function mayReceiveUpdates(timeoutMs?: number): Promise<boolean> {
  return (await verifyUpdateAccess(timeoutMs)).allowed;
}

export function updateAccessError(decision: UpdateAccessDecision): string {
  switch (decision.reason) {
    case "revoked": return "Updates are disabled for this account. Contact your administrator.";
    case "inactive": return "This account is inactive. Contact your administrator.";
    case "signed-out": return "Sign in to check for updates.";
    case "unconfigured": return "The account service is not configured.";
    case "timeout": return "Account verification timed out. Loom will retry automatically.";
    default: return "The account service could not verify update access. Loom will retry automatically.";
  }
}
