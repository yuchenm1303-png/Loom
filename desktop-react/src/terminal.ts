export interface TerminalSession {
  processId: string;
  workspace: string;
  cwd: string;
  shell: string;
  title: string;
  command: string;
  language: string;
  running: boolean;
  state: string;
  returnCode: number | null;
  failure: string;
  pty: boolean;
}

export interface TerminalRead extends TerminalSession {
  output: string;
  cursor: number;
  truncated: boolean;
  reset: boolean;
}

export interface TerminalRequest {
  workspace: string;
  processId?: string;
  command?: string;
  language?: string;
}

export const OPEN_TERMINAL_EVENT = "loom:open-terminal";
export function openTerminal(request: TerminalRequest): void {
  window.dispatchEvent(
    new CustomEvent(OPEN_TERMINAL_EVENT, { detail: request }),
  );
}

export const EXECUTABLE_LANGUAGES = new Set([
  "bash",
  "bat",
  "batch",
  "cmd",
  "console",
  "powershell",
  "ps1",
  "pwsh",
  "sh",
  "shell",
  "terminal",
  "zsh",
  "python",
  "py",
  "python3",
  "javascript",
  "js",
  "node",
  "nodejs",
]);
