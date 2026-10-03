import { LoomAccountClient } from "./accountClient.js";

// Pairing, desktop login and relay refresh must share the same in-memory session.
if (!String(process.env.LOOM_ACCOUNT_API_BASE_URL || "").trim()) {
  process.env.LOOM_ACCOUNT_API_BASE_URL = "https://account.smirel.com/v1";
}
export const hostAccount = new LoomAccountClient();
