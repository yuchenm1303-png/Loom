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

class AccountHttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

function normalizeBaseUrl(value: string): string {
  return String(value || "").trim().replace(/\/+$/, "");
}

function configuredAccountBaseUrl(): string {
  const fromEnvironment = normalizeBaseUrl(process.env.LOOM_ACCOUNT_API_BASE_URL || "");
  if (fromEnvironment) return fromEnvironment;

  try {
    const configPath = path.join(app.getPath("home"), ".loom", "account-service.json");
    if (fsSync.existsSync(configPath)) {
      const parsed = JSON.parse(fsSync.readFileSync(configPath, "utf8")) as { baseUrl?: string };
      const fromFile = normalizeBaseUrl(parsed.baseUrl || "");
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
      throw new Error("Account service returned an incomplete sign-in session.");
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

  private snapshot(session: TokenSession | null): LoomAccountSnapshot {
    return {
      configured: this.configured,
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
      throw new Error("Loom Account Service is not configured.");
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15_000);
    try {
      const headers = new Headers(init.headers);
      headers.set("Accept", "application/json");
      if (init.body) headers.set("Content-Type", "application/json");
      if (accessToken) headers.set("Authorization", "Bearer " + accessToken);

      const response = await fetch(this.baseUrl + endpoint, {
        ...init,
        headers,
        signal: controller.signal,
      });
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

  async status(): Promise<LoomAccountSnapshot> {
    if (!this.configured) return this.snapshot(null);
    let session = await this.loadSession();
    if (!session) return this.snapshot(null);

    if (session.expiresAt <= Date.now() + 30_000) {
      try {
        session = await this.refresh(session);
      } catch {
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
      if (!(error instanceof AccountHttpError) || error.status !== 401) throw error;
      try {
        session = await this.refresh(session);
        return this.snapshot(session);
      } catch {
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
