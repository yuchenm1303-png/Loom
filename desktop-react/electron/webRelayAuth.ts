import { app, safeStorage } from "electron";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { hostAccount } from "./hostAccount.js";
import { handleHostChannel } from "./hostProcess.js";
import { loomHostLaunchMode, type LoomHostLaunchMode } from "./hostMode.js";
import { currentHostRuntimeProtocol, currentHostRuntimeVersion } from "./hostRuntime.js";

const DEFAULT_ACCOUNT_URL = "https://account.smirel.com/v1";
const DEVICE_ID_FILE = "loom-web-device-id";
// This protocol belongs to the tiny installed bootstrap/relay. Agent/runtime
// capability compatibility is reported separately as hostProtocol.
export const LOOM_BOOTSTRAP_PROTOCOL_VERSION = 1;

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
  const status = await hostAccount.status();
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

export type WebRelayDeviceIdentity = {
  deviceId: string;
  deviceName: string;
  platform: string;
  appVersion: string;
  hostVersion: string;
  hostMode: LoomHostLaunchMode;
  hostProtocol: number;
  bootstrapProtocol: number;
};

export type WebRelayAuth = WebRelayDeviceIdentity & {
  accessToken: string;
  userId: number;
  email: string;
};

/**
 * Stable, non-secret identity for this local Loom Host. This deliberately does
 * not require account authentication so loom.smirel.com can discover a freshly
 * installed Host before the user has opened the Desktop UI.
 */
export async function webRelayDeviceIdentity(): Promise<WebRelayDeviceIdentity> {
  return {
    deviceId: await deviceId(),
    deviceName: os.hostname(),
    platform: process.platform,
    appVersion: app.getVersion(),
    hostVersion: currentHostRuntimeVersion(),
    hostMode: loomHostLaunchMode(),
    hostProtocol: currentHostRuntimeProtocol(),
    bootstrapProtocol: LOOM_BOOTSTRAP_PROTOCOL_VERSION,
  };
}

/**
 * Shared by the `loom:web-relay-auth` IPC channel (renderer) and the main-process
 * relay, so both agree on identity, device id, and token refresh behaviour.
 */
export async function webRelayAuthPayload(): Promise<WebRelayAuth> {
  const [identity, device] = await Promise.all([
    readCurrentAccessToken(),
    webRelayDeviceIdentity(),
  ]);
  return { ...identity, ...device };
}

handleHostChannel("loom:web-relay-auth", () => webRelayAuthPayload());
