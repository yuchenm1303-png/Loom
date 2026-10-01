export interface LoomAccountUser {
  id: number;
  email: string;
  display_name?: string;
  status: string;
  role?: string;
  email_verified?: boolean;
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

export interface LoomAuthCapabilities {
  emailVerification: boolean;
  passwordReset: boolean;
  google: boolean;
  github: boolean;
  legacyRegistration: boolean;
}

export interface LoomAuthChallenge {
  id: string;
  email: string;
  purpose: "register" | "password_reset" | string;
  expires_in: number;
  resend_after: number;
}

/** Mirrors `AccountErrorPayload` in `desktop-react/electron/accountErrors.ts`. */
export interface LoomAccountError {
  code: string;
  message: string;
  status?: number;
}

export type LoomAccountResult =
  | { ok: true; snapshot: LoomAccountSnapshot }
  | { ok: false; error: LoomAccountError };

export type LoomAuthCapabilitiesResult =
  | { ok: true; capabilities: LoomAuthCapabilities }
  | { ok: false; error: LoomAccountError };

export type LoomAuthChallengeResult =
  | { ok: true; challenge: LoomAuthChallenge }
  | { ok: false; error: LoomAccountError };
