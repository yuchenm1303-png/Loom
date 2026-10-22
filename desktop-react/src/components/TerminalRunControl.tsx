import { LoaderCircle, Play, Send, Square, Terminal } from "./icons";
import { useCallback, useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import { EXECUTABLE_LANGUAGES, openTerminal } from "../terminal";
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
  output?: string;
  cursor?: number;
  truncated?: boolean;
}

const ANSI_ESCAPE = new RegExp("\\u001B(?:\\][^\\u0007]*(?:\\u0007|\\u001B\\\\)|\\[[0-?]*[ -/]*[@-~]|[@-_])", "g");

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
  const { language: uiLanguage } = useI18n();
  const zh = uiLanguage === "zh-CN";
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
  const cursor = useRef(0);
  const launching = useRef(false);
  const context = useRef("");
  context.current = `${workspace}\u0000${language}\u0000${command}`;
  useEffect(() => {
    setProcessId(""); setRunning(false); setOutput(""); setFailure(""); setReturnCode(null); cursor.current = 0;
  }, [workspace, language, command]);

  const applySnapshot = useCallback((snapshot: TerminalSnapshot) => {
    setProcessId(snapshot.processId || "");
    setRunning(Boolean(snapshot.running));
    setReturnCode(snapshot.returnCode ?? null);
    const chunk = cleanTerminalOutput(snapshot.output || "");
    if (chunk) setOutput((current) => (current + chunk).slice(-100_000));
    if (snapshot.cursor !== undefined) cursor.current = snapshot.cursor;
    if (snapshot.truncated) setOutput(current => `[${zh ? "早期输出已截断" : "Earlier output truncated"}]\n${current}`);
    if (snapshot.failure) setFailure(snapshot.failure);
    if (!snapshot.running && startedAtRef.current) {
      setElapsedMs(Date.now() - startedAtRef.current);
    }
  }, [zh]);

  useEffect(() => {
    if (!processId || !workspace) return;
    let disposed = false;
    let timer = 0;
    let exitPolls = 0;

    const poll = async () => {
      try {
        const snapshot = await window.loom.call<TerminalSnapshot>("terminal/session/read", {
          processId,
          workspace,
          cursor: cursor.current,
        });
        if (disposed) return;
        applySnapshot(snapshot);
        if (!snapshot.running) exitPolls += 1;
        if (snapshot.running || exitPolls < 3) timer = window.setTimeout(() => void poll(), 180);
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
  }, [applySnapshot, processId, workspace]);

  if (!executable || !workspace) return null;

  async function runCommand() {
    if (disabled || launching.current || running) return;
    launching.current = true;
    setStarting(true);
    setOutput("");
    setFailure("");
    setReturnCode(null);
    setElapsedMs(0);
    setInput("");
    setProcessId("");
    cursor.current = 0;
    startedAtRef.current = Date.now();
    const runContext = context.current;
    try {
      const snapshot = await window.loom.call<TerminalSnapshot>("terminal/session/create", {
        command,
        language,
        workspace,
        title: `${language} · ${command.split("\n")[0].slice(0, 35)}`,
      });
      if (context.current !== runContext) return;
      applySnapshot(snapshot);
    } catch (error) {
      setFailure(errorMessage(error));
      setRunning(false);
      setElapsedMs(Date.now() - startedAtRef.current);
    } finally {
      setStarting(false);
      launching.current = false;
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
      const snapshot = await window.loom.call<TerminalSnapshot>("terminal/session/write", {
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
    ? (zh ? "运行中" : "Running")
    : failure
      ? (zh ? "失败" : "Failed")
      : returnCode === null
        ? ""
        : `${zh ? "退出码" : "Exit"} ${returnCode}`;

  return (
    <>
      <div className="terminal-run-actions"><button
        type="button"
        className={`terminal-run-launch ${running ? "is-running" : ""}`}
        onClick={() => running ? void interrupt() : void runCommand()}
        disabled={disabled || starting}
        title={disabled ? (zh ? "等待此代码块生成完成" : "Wait for this code block to finish") : running ? (zh ? "停止命令" : "Stop command") : (zh ? "在项目目录运行" : "Run in project directory")}
        aria-label={running ? (zh ? "停止命令" : "Stop command") : (zh ? "运行代码" : "Run command")}
      >
        {starting ? <LoaderCircle size={12} /> : running ? <Square size={11} /> : <Play size={11} fill="currentColor" />}
        <span>{running ? (zh ? "停止" : "Stop") : (zh ? "运行" : "Run")}</span>
      </button><button type="button" className="terminal-run-open" disabled={disabled || starting} title={zh ? "在终端中打开并运行" : "Open and run in terminal"} aria-label={zh ? "在终端中运行" : "Run in terminal"} onClick={() => {
        if (!workspace) return;
        openTerminal(processId ? { workspace, processId } : { workspace, command, language });
      }}><Terminal size={12} /><span>{zh ? "终端" : "Terminal"}</span></button></div>

      {hasResult ? (
        <div className="terminal-run-panel">
          <div className="terminal-run-status">
            <span>{zh ? "终端输出" : "Terminal output"}</span>
            <span className={failure || (returnCode !== null && returnCode !== 0) ? "is-error" : running ? "is-running" : "is-success"}>
              {status}{!running && !starting && elapsedMs ? ` · ${formatElapsed(elapsedMs)}` : ""}
            </span>
          </div>
          <pre className="terminal-run-output" aria-live="off">{output || (running || starting ? (zh ? "正在启动…" : "Starting…") : failure || (zh ? "无输出" : "No output"))}</pre>
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
                placeholder={zh ? "输入内容并按 Enter" : "Type input and press Enter"}
                aria-label={zh ? "终端输入" : "Terminal input"}
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
