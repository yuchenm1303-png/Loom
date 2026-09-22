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
  /** Keep Electron native controls in sync with Loom theme. */
  setNativeTheme(source: "system" | "light" | "dark"): Promise<"light" | "dark">;
  accountStatus<T = unknown>(): Promise<T>;
  accountLogin<T = unknown>(email: string, password: string): Promise<T>;
  accountRegister<T = unknown>(email: string, password: string): Promise<T>;
  accountLogout<T = unknown>(): Promise<T>;
  listModels<T = unknown>(): Promise<T>;
  setModelProviderKey<T = unknown>(provider: string, apiKey: string): Promise<T>;
  switchModelProfile<T = unknown>(selection: string): Promise<T>;
  switchModelProfile<T = unknown>(threadId: string, selection: string): Promise<T>;
  switchCurrentModel<T = unknown>(model: string): Promise<T>;
  switchCurrentModel<T = unknown>(threadId: string, selection: string, model: string): Promise<T>;
  addModel<T = unknown>(input: Record<string, unknown>): Promise<T>;
  addModel<T = unknown>(threadId: string, input: Record<string, unknown>): Promise<T>;
  updateModel<T = unknown>(input: Record<string, unknown>): Promise<T>;
  testModel<T = unknown>(selection: string): Promise<T>;
  deleteModel<T = unknown>(selection: string): Promise<T>;
  setReasoning<T = unknown>(kind: string, value: string): Promise<T>;
  setReasoning<T = unknown>(threadId: string, selection: string, model: string, kind: string, value: string): Promise<T>;
  /** Create a zip archive containing local Computer Use diagnostics and traces. */
  exportComputerLogs(): Promise<ComputerLogExportResult>;
  /** Create a zip archive containing local Browser Use diagnostics and bridge traces. */
  exportBrowserLogs(): Promise<BrowserLogExportResult>;
  setupBrowserExtension(browser?: "edge" | "chrome", extensionConnected?: boolean): Promise<{ ok: boolean; desiredVersion: string; manualInstallRequired: boolean; automaticUpdateRequested: boolean; extensionPath: string; pathCopied: boolean; folderOpened: boolean; folderError?: string; managementUrl: string; openError?: string }>;
  /** Open a folder or reveal a file in the native file manager. */
  revealPath(targetPath: string): Promise<boolean>;
  /** Copy an image source to the native OS clipboard. */
  copyImageSource(source: string): Promise<boolean>;
  /** Open a safe http(s) URL in the system browser. */
  openExternal(url: string): Promise<boolean>;
  /** Read a workspace-confined local image for safe in-app preview. */
  readLocalImage(targetPath: string, workspaceRoot: string): Promise<{
    dataUrl: string;
    path: string;
    name: string;
    size: number;
    mimeType: string;
  }>;
  /** Read a workspace-confined audio/video file on demand for in-app playback. */
  readLocalMedia(targetPath: string, workspaceRoot: string): Promise<{
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
