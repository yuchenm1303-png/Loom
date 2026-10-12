export interface UsageWindow {
  label: string;
  remainingPercent: number;
  resetsAt?: number;
}
export interface UsageBalance {
  currency: string;
  total: string;
  toppedUp?: string;
  granted?: string;
}
export interface ModelUsage {
  status: "ok" | "unsupported" | "unconfigured" | "error";
  provider: string;
  checkedAt: number;
  windows: UsageWindow[];
  balances: UsageBalance[];
  message?: string;
}
