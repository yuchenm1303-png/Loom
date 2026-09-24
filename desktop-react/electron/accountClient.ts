import { app, safeStorage } from "electron";
import fs from "node:fs/promises";
import fsSync from "node:fs";
import path from "node:path";

export interface LoomAccountUser {
  id: number;
  email: string;
  display_name?: string;
  status: string;
  created_at?: number;
}

export interface LoomAccountSnapshot {
  configured: boolean;
  /** False when the service could not be reached, as opposed to rejecting us. */
  reachable: boolean;
  authenticated: boolean;
  user: LoomAccountUser | null;
  serviceUrl: string;
}

interface TokenSession {
  accessToken: string;
  refreshToken: string;
  expiresAt: number;
  user: LoomAccountUser;
}

interface AuthResponse {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  token_type?: string;
  user: LoomAccountUser;
}

/**
 * Carries the account service's machine readable `code` up to the IPC layer.
 * A `status` of 0 means the request never produced an HTTP response — the
 * service was unconfigured, unreachable, or timed out.
 */
export class AccountHttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "AccountHttpError";
  }
}

function normalizeBaseUrl(value: string): string {
  return String(value || "").trim().replace(/\/+$/, "");
}

function safeAccountBaseUrl(value: string): string {
  const normalized = normalizeBaseUrl(value);
  if (!normalized) return "";
  try {
    const parsed = new URL(normalized);
    const loopback = parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost" || parsed.hostname === "::1";
    if (parsed.protocol === "https:" || loopback) return normalized;
  } catch {
    return "";
  }
  return "";
}

/**
 * A rejection means the credential itself is no longer accepted, so the stored
 * session must be discarded. Everything else — 5xx, timeouts, DNS failures —
 * is treated as an outage that the stored session may survive.
 */
function isAuthRejection(error: unknown): boolean {
  return error instanceof AccountHttpError && (error.status === 401 || error.status === 403);
}

function configuredAccountBaseUrl(): string {
  const fromEnvironment = safeAccountBaseUrl(process.env.LOOM_ACCOUNT_API_BASE_URL || "");
  if (fromEnvironment) return fromEnvironment;

  try {
    const configPath = path.join(app.getPath("home"), ".loom", "account-service.json");
    if (fsSync.existsSync(configPath)) {
      const parsed = JSON.parse(fsSync.readFileSync(configPath, "utf8")) as { baseUrl?: string };
      const fromFile = safeAccountBaseUrl(parsed.baseUrl || "");
      if (fromFile) return fromFile;
    }
  } catch {
    // A malformed optional config file must not stop Loom from starting.
  }

  return app.isPackaged ? "" : "http://127.0.0.1:8787/v1";
}

export class LoomAccountClient {
  private memorySession: TokenSession | null = null;
  private readonly baseUrl: string;

  constructor() {
    this.baseUrl = configuredAccountBaseUrl();
  }

  private get configured(): boolean {
    return Boolean(this.baseUrl);
  }

  private sessionPath(): string {
    return path.join(app.getPath("userData"), "loom-account-session.bin");
  }

  private async loadSession(): Promise<TokenSession | null> {
    if (this.memorySession) return this.memorySession;
    if (!safeStorage.isEncryptionAvailable()) return null;
    try {
      const encrypted = await fs.readFile(this.sessionPath());
      const parsed = JSON.parse(safeStorage.decryptString(encrypted)) as Partial<TokenSession>;
      if (
        !parsed
        || typeof parsed.accessToken !== "string"
        || typeof parsed.refreshToken !== "string"
        || typeof parsed.expiresAt !== "number"
        || !parsed.user
      ) {
        return null;
      }
      this.memorySession = parsed as TokenSession;
      return this.memorySession;
    } catch {
      return null;
    }
  }

  private async saveSession(response: AuthResponse): Promise<TokenSession> {
    const session: TokenSession = {
      accessToken: String(response.access_token || ""),
      refreshToken: String(response.refresh_token || ""),
      expiresAt: Date.now() + Math.max(1, Number(response.expires_in || 900)) * 1000,
      user: response.user,
    };
    if (!session.accessToken || !session.refreshToken || !session.user) {
      throw new AccountHttpError(
        0,
        "ACCOUNT_RESPONSE_INVALID",
        "Account service returned an incomplete sign-in session.",
      );
    }

    this.memorySession = session;
    if (safeStorage.isEncryptionAvailable()) {
      const target = this.sessionPath();
      await fs.mkdir(path.dirname(target), { recursive: true });
      await fs.writeFile(target, safeStorage.encryptString(JSON.stringify(session)), { mode: 0o600 });
    }
    return session;
  }

  private async clearSession(): Promise<void> {
    this.memorySession = null;
    try {
      await fs.rm(this.sessionPath(), { force: true });
    } catch {
      // Local sign-out is authoritative even when the old session file is missing.
    }
  }

  private snapshot(session: TokenSession | null, reachable = this.configured): LoomAccountSnapshot {
    return {
      configured: this.configured,
      reachable,
      authenticated: Boolean(session?.user),
      user: session?.user ?? null,
      serviceUrl: this.baseUrl,
    };
  }

  private async request<T>(
    endpoint: string,
    init: RequestInit = {},
    accessToken = "",
  ): Promise<T> {
    if (!this.configured) {
      throw new AccountHttpError(
        0,
        "ACCOUNT_SERVICE_UNCONFIGURED",
        "Loom Account Service is not configured.",
      );
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15_000);
    try {
      const headers = new Headers(init.headers);
      headers.set("Accept", "application/json");
      if (init.body) headers.set("Content-Type", "application/json");
      if (accessToken) headers.set("Authorization", "Bearer " + accessToken);

      let response: Response;
      try {
        response = await fetch(this.baseUrl + endpoint, {
          ...init,
          headers,
          signal: controller.signal,
        });
      } catch (cause) {
        // Distinguish "the service never answered" from "the service answered
        // with an error", so the UI can tell the user which one to fix.
        if (cause instanceof Error && cause.name === "AbortError") {
          throw new AccountHttpError(
            0,
            "ACCOUNT_REQUEST_TIMEOUT",
            "The account service did not respond in time.",
          );
        }
        throw new AccountHttpError(
          0,
          "ACCOUNT_SERVICE_UNREACHABLE",
          "Could not reach the account service.",
        );
      }
      const body = await response.json().catch(() => ({})) as {
        error?: { code?: string; message?: string };
      } & T;
      if (!response.ok) {
        throw new AccountHttpError(
          response.status,
          String(body.error?.code || "ACCOUNT_REQUEST_FAILED"),
          String(body.error?.message || ("Account request failed (" + response.status + ").")),
        );
      }
      return body as T;
    } finally {
      clearTimeout(timer);
    }
  }

  private async refresh(session: TokenSession): Promise<TokenSession> {
    const response = await this.request<AuthResponse>("/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: session.refreshToken }),
    });
    return this.saveSession(response);
  }

  /**
   * Liveness probe. `/auth/me` with no token answers 401 MISSING_TOKEN, which
   * still proves the service is answering — so any HTTP response counts as
   * reachable and only a transport failure counts as an outage. `/auth/me` is
   * not rate limited, so this is safe to call on every status check.
   */
  private async probe(): Promise<boolean> {
    try {
      await this.request("/auth/me", { method: "GET" });
      return true;
    } catch (error) {
      return error instanceof AccountHttpError && error.status > 0;
    }
  }

  async status(): Promise<LoomAccountSnapshot> {
    if (!this.configured) return this.snapshot(null, false);
    let session = await this.loadSession();

    if (!session) {
      // No stored credential — but the UI still has to distinguish "signed out"
      // from "cannot reach the service", so probe instead of assuming the
      // service is up just because a URL is configured.
      return this.snapshot(null, await this.probe());
    }

    if (session.expiresAt <= Date.now() + 30_000) {
      try {
        session = await this.refresh(session);
      } catch (error) {
        // Only a rejected credential means the session is dead. A transport
        // failure is an outage, and deleting the stored session for it would
        // sign the user out over a dropped connection.
        if (!isAuthRejection(error)) return this.snapshot(session, false);
        await this.clearSession();
        return this.snapshot(null);
      }
    }

    try {
      const result = await this.request<{ user: LoomAccountUser }>(
        "/auth/me",
        { method: "GET" },
        session.accessToken,
      );
      session = { ...session, user: result.user };
      this.memorySession = session;
      return this.snapshot(session);
    } catch (error) {
      if (!isAuthRejection(error)) return this.snapshot(session, false);
      try {
        session = await this.refresh(session);
        return this.snapshot(session);
      } catch (refreshError) {
        if (!isAuthRejection(refreshError)) return this.snapshot(session, false);
        await this.clearSession();
        return this.snapshot(null);
      }
    }
  }

  async login(email: string, password: string): Promise<LoomAccountSnapshot> {
    const response = await this.request<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    return this.snapshot(await this.saveSession(response));
  }

  async register(email: string, password: string): Promise<LoomAccountSnapshot> {
    const response = await this.request<AuthResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    return this.snapshot(await this.saveSession(response));
  }

  async logout(): Promise<LoomAccountSnapshot> {
    const session = await this.loadSession();
    if (session && this.configured) {
      try {
        await this.request<{ ok: boolean }>("/auth/logout", {
          method: "POST",
          body: JSON.stringify({ refresh_token: session.refreshToken }),
        });
      } catch {
        // Always clear the local credential even if the service is temporarily offline.
      }
    }
    await this.clearSession();
    return this.snapshot(null);
  }
}
