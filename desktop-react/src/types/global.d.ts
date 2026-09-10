export interface LoomNotification {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
}

export interface LoomBridge {
  connect(): Promise<unknown>;
  call<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T>;
  disconnect(): Promise<void>;
  listModels<T = unknown>(): Promise<T>;
  switchModelProfile<T = unknown>(selection: string): Promise<T>;
  switchCurrentModel<T = unknown>(model: string): Promise<T>;
  addModel<T = unknown>(input: Record<string, unknown>): Promise<T>;
  setReasoning<T = unknown>(kind: string, value: string): Promise<T>;
  /** Native folder picker. Resolves to "" when the user cancels. */
  pickDirectory(): Promise<string>;
  onNotification(listener: (payload: LoomNotification) => void): () => void;
}

declare global {
  interface Window {
    loom: LoomBridge;
  }
}

export {};
