import { app, ipcMain, safeStorage } from "electron";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { LoomAccountClient } from "./accountClient.js";

const DEFAULT_ACCOUNT_URL = "https://account.smirel.com/v1";
const DEVICE_ID_FILE = "loom-web-device-id";

if (!String(process.env.LOOM_ACCOUNT_API_BASE_URL || "").trim()) {
  process.env.LOOM_ACCOUNT_API_BASE_URL = DEFAULT_ACCOUNT_URL;
}


async function deviceId(): Promise<string> {
  const target = path.join(app.getPath("userData"), DEVICE_ID_FILE);
  try {
    const current = (await fs.readFile(target, "utf8")).trim();
    if (current.length >= 24) return current;
  } catch {}
  const value = crypto.randomUUID();
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(target, value, { encoding: "utf8", mode: 0o600 });
  return value;
}

async function readCurrentAccessToken(): Promise<{ accessToken: string; userId: number; email: string }> {
  const status = await new LoomAccountClient().status();
  if (!status.authenticated || !status.user || !safeStorage.isEncryptionAvailable()) {
    throw new Error("Sign in to Loom before using Loom Web.");
  }
  const target = path.join(app.getPath("userData"), "loom-account-session.bin");
  const encrypted = await fs.readFile(target);
  const session = JSON.parse(safeStorage.decryptString(encrypted)) as { accessToken?: string };
  const accessToken = String(session.accessToken || "").trim();
  if (!accessToken) throw new Error("Loom account session is unavailable.");
  return { accessToken, userId: Number(status.user.id), email: String(status.user.email || "") };
}

ipcMain.handle("loom:web-relay-auth", async () => {
  const identity = await readCurrentAccessToken();
  return {
    ...identity,
    deviceId: await deviceId(),
    deviceName: os.hostname(),
    platform: process.platform,
    appVersion: app.getVersion(),
  };
});
