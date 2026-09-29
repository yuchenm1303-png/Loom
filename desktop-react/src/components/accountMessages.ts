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
  EMAIL_NOT_VERIFIED: "这个邮箱还没有完成验证，请先输入邮箱验证码。",
  EMAIL_ALREADY_VERIFIED: "这个邮箱已经完成验证，直接登录即可。",
  EMAIL_DELIVERY_UNAVAILABLE: "邮箱验证服务尚未配置，请稍后再试。",
  EMAIL_DELIVERY_FAILED: "验证码邮件发送失败，请稍后重试。",
  INVALID_VERIFICATION_CODE: "验证码不正确，请检查后重试。",
  VERIFICATION_CODE_EXPIRED: "验证码已过期，请重新发送。",
  VERIFICATION_ATTEMPTS_EXCEEDED: "验证码错误次数过多，请重新发送一个新验证码。",
  VERIFICATION_COOLDOWN: "验证码刚刚已经发送，请稍等片刻再重新发送。",
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
  EMAIL_NOT_VERIFIED: "Verify this email address before signing in.",
  EMAIL_ALREADY_VERIFIED: "This email is already verified. Sign in instead.",
  EMAIL_DELIVERY_UNAVAILABLE: "Email verification is not configured on this server yet.",
  EMAIL_DELIVERY_FAILED: "The verification email could not be sent. Try again shortly.",
  INVALID_VERIFICATION_CODE: "That verification code is not correct.",
  VERIFICATION_CODE_EXPIRED: "That verification code has expired. Send a new one.",
  VERIFICATION_ATTEMPTS_EXCEEDED: "Too many incorrect codes. Send a new verification code.",
  VERIFICATION_COOLDOWN: "A code was just sent. Wait a moment before sending another.",
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
