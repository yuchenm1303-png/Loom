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
