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

/** Mirrors `AccountErrorPayload` in `desktop-react/electron/accountErrors.ts`. */
export interface LoomAccountError {
  code: string;
  message: string;
  status?: number;
}

/**
 * Shape returned by the account IPC handlers. They resolve instead of rejecting
 * so the service's error `code` survives the IPC boundary.
 */
export type LoomAccountResult =
  | { ok: true; snapshot: LoomAccountSnapshot }
  | { ok: false; error: LoomAccountError };
