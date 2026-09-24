import type { LoomAccountError } from "../types/account";

/**
 * The account service answers with a stable machine readable `code` plus an
 * English `message`. Rendering the message directly put raw English inside a
 * Chinese UI, so every code the service (or the desktop client) can produce is
 * mapped here. Unknown codes fall back to the service's own message rather than
 * swallowing it.
 */
const ZH: Record<string, string> = {
  INVALID_EMAIL: "邮箱格式不正确，请检查后重试。",
  WEAK_PASSWORD: "密码长度需要在 8 到 512 位之间。",
  EMAIL_EXISTS: "该邮箱已经注册过了，直接登录即可。",
  INVALID_CREDENTIALS: "邮箱或密码不正确。",
  ACCOUNT_DISABLED: "该账号已被停用。",
  RATE_LIMITED: "尝试次数过多，请稍后再试。",
  INVALID_TOKEN: "登录状态已失效，请重新登录。",
  INVALID_REFRESH_TOKEN: "登录状态已失效，请重新登录。",
  MISSING_TOKEN: "登录状态已失效，请重新登录。",
  REQUEST_TOO_LARGE: "请求内容过大，请缩短后重试。",
  INVALID_JSON: "请求格式不正确。",
  NOT_FOUND: "账号服务接口不存在，请检查服务地址。",
  ACCOUNT_CREATE_FAILED: "账号创建失败，请稍后重试。",
  ACCOUNT_SERVICE_UNCONFIGURED: "尚未配置账号服务地址。",
  ACCOUNT_SERVICE_UNREACHABLE: "无法连接账号服务，请检查网络或服务状态。",
  ACCOUNT_REQUEST_TIMEOUT: "账号服务响应超时，请稍后重试。",
  ACCOUNT_REQUEST_FAILED: "账号请求失败，请稍后重试。",
  ACCOUNT_RESPONSE_INVALID: "账号服务返回了不完整的数据。",
};

const EN: Record<string, string> = {
  INVALID_EMAIL: "That email address is not valid.",
  WEAK_PASSWORD: "A password must be between 8 and 512 characters.",
  EMAIL_EXISTS: "An account with this email already exists. Sign in instead.",
  INVALID_CREDENTIALS: "Email or password is incorrect.",
  ACCOUNT_DISABLED: "This account has been disabled.",
  RATE_LIMITED: "Too many attempts. Try again shortly.",
  INVALID_TOKEN: "Your session has expired. Sign in again.",
  INVALID_REFRESH_TOKEN: "Your session has expired. Sign in again.",
  MISSING_TOKEN: "Your session has expired. Sign in again.",
  REQUEST_TOO_LARGE: "That request was too large.",
  INVALID_JSON: "The request was malformed.",
  NOT_FOUND: "The account service endpoint was not found. Check the service URL.",
  ACCOUNT_CREATE_FAILED: "The account could not be created. Try again shortly.",
  ACCOUNT_SERVICE_UNCONFIGURED: "No account service URL is configured.",
  ACCOUNT_SERVICE_UNREACHABLE: "Could not reach the account service. Check your network or the service status.",
  ACCOUNT_REQUEST_TIMEOUT: "The account service did not respond in time.",
  ACCOUNT_REQUEST_FAILED: "The account request failed. Try again shortly.",
  ACCOUNT_RESPONSE_INVALID: "The account service returned an incomplete response.",
};

export function accountErrorText(error: LoomAccountError | null, zh: boolean): string {
  if (!error) return "";
  const table = zh ? ZH : EN;
  const known = table[error.code];
  if (known) return known;
  return error.message || (zh ? "账号请求失败，请稍后重试。" : "The account request failed.");
}

/** Host (plus path) of the configured service, for the "which server" line. */
export function accountServiceLabel(serviceUrl: string): string {
  const value = String(serviceUrl || "").trim();
  if (!value) return "";
  try {
    const parsed = new URL(value);
    return (parsed.host + parsed.pathname).replace(/\/+$/, "");
  } catch {
    return value.replace(/^https?:\/\//, "").replace(/\/+$/, "");
  }
}

/**
 * Mirrors the service's own rule (`_EMAIL_RE` in `services/loom_account/server.py`):
 * a non-empty local part, an "@", a non-empty domain, and a dot in the domain.
 */
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isValidAccountEmail(value: string): boolean {
  const email = value.trim();
  return email.length <= 254 && EMAIL_RE.test(email);
}

export const ACCOUNT_PASSWORD_MIN_LENGTH = 8;
export const ACCOUNT_PASSWORD_MAX_LENGTH = 512;
