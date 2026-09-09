export interface LoomNotification {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
}

export interface LoomBridge {
  connect(): Promise<unknown>;
  call<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T>;
  disconnect(): Promise<void>;
  onNotification(listener: (payload: LoomNotification) => void): () => void;
}

declare global {
  interface Window {
    loom: LoomBridge;
  }
}

export {};
