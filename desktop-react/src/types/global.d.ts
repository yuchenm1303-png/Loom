export interface LoomNotification {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
}

export interface DiagnosticLogExportResult {
  ok: boolean;
  cancelled?: boolean;
  kind?: "computer" | "browser" | string;
  archivePath?: string;
  logDir?: string;
  python?: string;
  fileCount?: number;
  sizeBytes?: number;
}

export type ComputerLogExportResult = DiagnosticLogExportResult;
export type BrowserLogExportResult = DiagnosticLogExportResult;

export interface LoomBridge {
  connect(): Promise<unknown>;
  call<T = unknown>(method: string, params?: Record<string, unknown>): Promise<T>;
  disconnect(): Promise<void>;
  listModels<T = unknown>(): Promise<T>;
  switchModelProfile<T = unknown>(selection: string): Promise<T>;
  switchCurrentModel<T = unknown>(model: string): Promise<T>;
  addModel<T = unknown>(input: Record<string, unknown>): Promise<T>;
  updateModel<T = unknown>(input: Record<string, unknown>): Promise<T>;
  testModel<T = unknown>(selection: string): Promise<T>;
  deleteModel<T = unknown>(selection: string): Promise<T>;
  setReasoning<T = unknown>(kind: string, value: string): Promise<T>;
  /** Create a zip archive containing local Computer Use diagnostics and traces. */
  exportComputerLogs(): Promise<ComputerLogExportResult>;
  /** Create a zip archive containing local Browser Use diagnostics and bridge traces. */
  exportBrowserLogs(): Promise<BrowserLogExportResult>;
  setupBrowserExtension(browser?: "edge" | "chrome", extensionConnected?: boolean): Promise<{ ok: boolean; desiredVersion: string; manualInstallRequired: boolean; automaticUpdateRequested: boolean; extensionPath: string; pathCopied: boolean; folderOpened: boolean; folderError?: string; managementUrl: string; openError?: string }>;
  /** Open a folder or reveal a file in the native file manager. */
  revealPath(targetPath: string): Promise<boolean>;
  /** Read a workspace-confined local image for safe in-app preview. */
  readLocalImage(targetPath: string, workspaceRoot: string): Promise<{
    dataUrl: string;
    path: string;
    name: string;
    size: number;
    mimeType: string;
  }>;
  /** Native folder picker. Resolves to "" when the user cancels. */
  pickDirectory(): Promise<string>;
  /** Native file picker. Resolves to [] when the user cancels. */
  pickFiles(): Promise<string[]>;
  /** Apply Chromium's native page zoom and return the clamped factor. */
  setZoomFactor(factor: number): number;
  /** Absolute path of a dropped/picked File, or "" when unavailable. */
  filePathFor(file: File): string;
  /** Write bytes to a temp file and return its path. */
  stageTempFile(name: string, bytes: Uint8Array): Promise<string>;
  onNotification(listener: (payload: LoomNotification) => void): () => void;
}

declare global {
  interface Window {
    loom: LoomBridge;
  }
}

export {};
