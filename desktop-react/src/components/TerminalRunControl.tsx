import { LoaderCircle, Play, Send, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import "./terminal-run-control.css";

interface TerminalSnapshot {
  processId: string;
  state: string;
  running: boolean;
  returnCode: number | null;
  stdoutDelta?: string;
  stderrDelta?: string;
  timedOut?: boolean;
  outputTruncated?: boolean;
  failure?: string;
}

const EXECUTABLE_LANGUAGES = new Set([
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
]);

const ANSI_ESCAPE = new RegExp("\\u001B(?:[@-_]|\\[[0-?]*[ -/]*[@-~])", "g");

function cleanTerminalOutput(value: string): string {
  return String(value || "")
    .replace(ANSI_ESCAPE, "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n");
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return String(error || "Terminal command failed");
}

function formatElapsed(milliseconds: number): string {
  if (milliseconds < 1000) return `${Math.max(1, Math.round(milliseconds))}ms`;
  return `${(milliseconds / 1000).toFixed(milliseconds < 10_000 ? 1 : 0)}s`;
}

export function TerminalRunControl({
  language,
  command,
  workspace,
  disabled = false,
}: {
  language: string;
  command: string;
  workspace?: string;
  disabled?: boolean;
}) {
  const executable = EXECUTABLE_LANGUAGES.has(language.toLowerCase());
  const [processId, setProcessId] = useState("");
  const [running, setRunning] = useState(false);
  const [output, setOutput] = useState("");
  const [failure, setFailure] = useState("");
  const [returnCode, setReturnCode] = useState<number | null>(null);
  const [elapsedMs, setElapsedMs] = useState(0);
  const [input, setInput] = useState("");
  const [starting, setStarting] = useState(false);
  const startedAtRef = useRef(0);

  const applySnapshot = useCallback((snapshot: TerminalSnapshot) => {
    setProcessId(snapshot.processId || "");
    setRunning(Boolean(snapshot.running));
    setReturnCode(snapshot.returnCode ?? null);
    const chunk = cleanTerminalOutput(`${snapshot.stdoutDelta || ""}${snapshot.stderrDelta || ""}`);
    if (chunk) setOutput((current) => current + chunk);
    if (snapshot.failure) setFailure(snapshot.failure);
    if (!snapshot.running && startedAtRef.current) {
      setElapsedMs(Date.now() - startedAtRef.current);
    }
  }, []);

  useEffect(() => {
    if (!processId || !running || !workspace) return;
    let disposed = false;
    let timer = 0;

    const poll = async () => {
      try {
        const snapshot = await window.loom.call<TerminalSnapshot>("terminal/read", {
          processId,
          workspace,
        });
        if (disposed) return;
        applySnapshot(snapshot);
        if (snapshot.running) timer = window.setTimeout(() => void poll(), 180);
      } catch (error) {
        if (disposed) return;
        setRunning(false);
        setFailure(errorMessage(error));
        if (startedAtRef.current) setElapsedMs(Date.now() - startedAtRef.current);
      }
    };

    timer = window.setTimeout(() => void poll(), 120);
    return () => {
      disposed = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [applySnapshot, processId, running, workspace]);

  if (!executable || !workspace) return null;

  async function runCommand() {
    if (disabled || starting || running) return;
    setStarting(true);
    setOutput("");
    setFailure("");
    setReturnCode(null);
    setElapsedMs(0);
    setInput("");
    setProcessId("");
    startedAtRef.current = Date.now();
    try {
      const snapshot = await window.loom.call<TerminalSnapshot>("terminal/run", {
        command,
        language,
        workspace,
      });
      applySnapshot(snapshot);
    } catch (error) {
      setFailure(errorMessage(error));
      setRunning(false);
      setElapsedMs(Date.now() - startedAtRef.current);
    } finally {
      setStarting(false);
    }
  }

  async function interrupt() {
    if (!processId || !workspace) return;
    try {
      const snapshot = await window.loom.call<TerminalSnapshot>("terminal/interrupt", {
        processId,
        workspace,
      });
      applySnapshot(snapshot);
    } catch (error) {
      setFailure(errorMessage(error));
    }
  }

  async function sendInput() {
    const value = input;
    if (!value || !processId || !workspace || !running) return;
    setInput("");
    try {
      const snapshot = await window.loom.call<TerminalSnapshot>("terminal/write", {
        processId,
        workspace,
        text: `${value}\r`,
      });
      applySnapshot(snapshot);
    } catch (error) {
      setFailure(errorMessage(error));
    }
  }

  const hasResult = Boolean(processId || output || failure || returnCode !== null);
  const status = running || starting
    ? "Running"
    : failure
      ? "Failed"
      : returnCode === null
        ? ""
        : `Exit ${returnCode}`;

  return (
    <>
      <button
        type="button"
        className={`terminal-run-launch ${running ? "is-running" : ""}`}
        onClick={() => running ? void interrupt() : void runCommand()}
        disabled={disabled || starting}
        title={disabled ? "Wait for the response to finish" : running ? "Stop command" : "Run in project terminal"}
        aria-label={running ? "Stop command" : "Run command"}
      >
        {starting ? <LoaderCircle size={12} className="terminal-run-spinner" /> : running ? <Square size={11} /> : <Play size={11} fill="currentColor" />}
        <span>{running ? "Stop" : "Run"}</span>
      </button>

      {hasResult ? (
        <div className="terminal-run-panel">
          <div className="terminal-run-status">
            <span>Terminal</span>
            <span className={failure || (returnCode !== null && returnCode !== 0) ? "is-error" : running ? "is-running" : "is-success"}>
              {status}{!running && !starting && elapsedMs ? ` · ${formatElapsed(elapsedMs)}` : ""}
            </span>
          </div>
          <pre className="terminal-run-output">{output || (running || starting ? "Starting…" : failure || "No output")}</pre>
          {failure ? <div className="terminal-run-error">{failure}</div> : null}
          {running ? (
            <div className="terminal-run-input-row">
              <input
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void sendInput();
                  }
                }}
                placeholder="Type input and press Enter"
                aria-label="Terminal input"
              />
              <button type="button" onClick={() => void sendInput()} disabled={!input} title="Send input" aria-label="Send terminal input">
                <Send size={12} />
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
