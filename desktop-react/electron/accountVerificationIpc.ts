import { ipcMain } from "electron";
import {
  AccountHttpError,
  LoomAccountClient,
  type LoomAccountSnapshot,
} from "./accountClient.js";

interface AccountErrorPayload {
  code: string;
  message: string;
  status?: number;
}

type AccountIpcResult =
  | { ok: true; snapshot: LoomAccountSnapshot }
  | { ok: false; error: AccountErrorPayload };

const accountClient = new LoomAccountClient();

function accountErrorPayload(error: unknown): AccountErrorPayload {
  if (error instanceof AccountHttpError) {
    return {
      code: error.code,
      message: error.message,
      status: error.status || undefined,
    };
  }
  return {
    code: "ACCOUNT_REQUEST_FAILED",
    message: error instanceof Error ? error.message : "Account request failed.",
  };
}

async function runAccountAction(
  action: () => Promise<LoomAccountSnapshot>,
): Promise<AccountIpcResult> {
  try {
    return { ok: true, snapshot: await action() };
  } catch (error) {
    return { ok: false, error: accountErrorPayload(error) };
  }
}

ipcMain.handle("loom:account-verify-email", (_event, email: string, code: string) =>
  runAccountAction(() => accountClient.verifyEmail(String(email || ""), String(code || ""))),
);

ipcMain.handle("loom:account-resend-verification", (_event, email: string) =>
  runAccountAction(() => accountClient.resendVerification(String(email || ""))),
);
