import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, ChevronUp, CircleAlert, Maximize2, RefreshCw, Square, Terminal, X } from "./icons";
import { useI18n } from "../i18n";
import "./background-tasks.css";

interface BackgroundProcess {
  processId: string;
  state: string;
  running: boolean;
  returnCode: number | null;
  argv: string[];
  cwd: string;
  backend: string;
  pty: boolean;
  elapsedSeconds: number;
  outputTruncated: boolean;
  failure: string;
}

interface TaskDetail {
  processId: string;
  state: string;
  running: boolean;
  stdout: string;
  stderr: string;
  returnCode: number | null;
  outputTruncated: boolean;
  failure: string;
}

function duration(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  if (whole < 60) return whole + "s";
  if (whole < 3600) return Math.floor(whole / 60) + "m " + String(whole % 60).padStart(2, "0") + "s";
  return Math.floor(whole / 3600) + "h " + String(Math.floor(whole % 3600 / 60)).padStart(2, "0") + "m";
}

function commandName(argv: string[]): string {
  // The final argument to -c/-Command is usually the human readable shell command.
  const shellArgument = argv.findIndex(part => ["-c", "-lc", "-Command"].includes(part));
  const name = shellArgument >= 0 ? argv[shellArgument + 1] : argv.join(" ");
  return (name || argv.join(" ") || "Command").trim().slice(0, 250);
}

export function BackgroundTaskPanel({
  workspace,
  connected,
  open,
  onClose,
  onRunningCount,
}: {
  workspace: string;
  connected: boolean;
  open: boolean;
  onClose(): void;
  onRunningCount(count: number): void;
}) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [processes, setProcesses] = useState<BackgroundProcess[]>([]);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [showFinished, setShowFinished] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [stoppingId, setStoppingId] = useState("");
  const [error, setError] = useState("");
  const running = processes.filter(process => process.running);
  const finished = processes.filter(process => !process.running);

  // Poll even when closed (at a lower frequency) so the header count is useful.
  // Do not share or consume the terminal/read delta cursor used by command cards.
  useEffect(() => {
    if (!workspace || !connected) {
      setProcesses([]);
      onRunningCount(0);
      return;
    }
    let disposed = false;
    let timeout = 0;
    const poll = async () => {
      try {
        const response = await window.loom.call<{ processes: BackgroundProcess[] }>("terminal/list", { workspace });
        if (disposed) return;
        const next = Array.isArray(response.processes) ? response.processes : [];
        setProcesses(next);
        onRunningCount(next.filter(process => process.running).length);
        setError("");
      } catch (cause) {
        if (disposed) return;
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (!disposed) timeout = window.setTimeout(() => void poll(), open ? 1800 : 7000);
      }
    };
    void poll();
    return () => {
      disposed = true;
      window.clearTimeout(timeout);
    };
  }, [workspace, connected, open, onRunningCount]);

  useEffect(() => {
    setSelectedId("");
    setDetail(null);
  }, [workspace]);

  useEffect(() => {
    if (!open || !selectedId || !workspace || !connected) return;
    let disposed = false;
    let timeout = 0;
    const poll = async () => {
      try {
        const response = await window.loom.call<TaskDetail>("terminal/detail", {
          processId: selectedId,
          workspace,
        });
        if (!disposed) setDetail(response);
      } catch (cause) {
        if (!disposed) setDetail({
          processId: selectedId, state: "unavailable", running: false,
          stdout: "", stderr: "", returnCode: null, outputTruncated: false,
          failure: cause instanceof Error ? cause.message : String(cause),
        });
      } finally {
        if (!disposed) timeout = window.setTimeout(() => void poll(), 1300);
      }
    };
    void poll();
    return () => {
      disposed = true;
      window.clearTimeout(timeout);
    };
  }, [open, selectedId, workspace, connected]);

  useEffect(() => {
    if (!open) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [open, onClose]);

  const interrupt = useCallback(async (processId: string) => {
    if (!workspace || stoppingId) return;
    setStoppingId(processId);
    try {
      await window.loom.call("terminal/interrupt", { processId, workspace });
      const response = await window.loom.call<{ processes: BackgroundProcess[] }>("terminal/list", { workspace });
      setProcesses(response.processes);
      onRunningCount(response.processes.filter(process => process.running).length);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setStoppingId("");
    }
  }, [workspace, stoppingId, onRunningCount]);

  if (!open) return null;
  const list = (items: BackgroundProcess[]) => items.map(process => (
    <li key={process.processId} className="background-task-item">
      <div className="background-task-item-main">
        <button
          type="button"
          className={"background-task-summary" + (selectedId === process.processId ? " is-selected" : "")}
          onClick={() => {
            setSelectedId(current => current === process.processId ? "" : process.processId);
            setDetail(null);
          }}
          aria-expanded={selectedId === process.processId}
          title={process.argv.join(" ")}
        >
          <span className="background-task-name">{commandName(process.argv)}</span>
          <span className="background-task-meta">
            <Terminal size={13} aria-hidden="true" /> {process.pty ? "Terminal" : "Command"}
            <span className={"background-task-state " + (process.running ? "is-running" : process.returnCode === 0 ? "is-success" : "is-failed")}>
              {process.running ? (zh ? "运行中" : "Running") : process.returnCode === 0 ? (zh ? "已完成" : "Completed") : (zh ? "已结束" : "Exited")}
            </span>
            <span>{duration(process.elapsedSeconds)}</span>
          </span>
        </button>
        {process.running ? (
          <button
            type="button"
            className="background-task-stop"
            onClick={() => void interrupt(process.processId)}
            disabled={Boolean(stoppingId)}
            title={zh ? "中断此命令" : "Interrupt command"}
            aria-label={zh ? "中断此命令" : "Interrupt command"}
          ><Square size={13} /></button>
        ) : null}
      </div>
      {selectedId === process.processId ? (
        <div className="background-task-detail">
          <div className="background-task-command">{process.argv.join(" ")}</div>
          <div className="background-task-location" title={process.cwd}>{process.cwd}</div>
          {detail && detail.processId === process.processId ? (
            <>
              <pre tabIndex={0} className="background-task-log">{[detail.stdout, detail.stderr].filter(Boolean).join("\n") || (detail.running ? (zh ? "等待输出…" : "Waiting for output…") : (detail.failure || (zh ? "无输出" : "No output")))}</pre>
              {detail.outputTruncated ? <small>{zh ? "日志过长，较早内容已被截断" : "Older output was truncated"}</small> : null}
              {detail.failure ? <small className="background-task-error">{detail.failure}</small> : null}
              {!detail.running && detail.returnCode !== null ? <small>{zh ? "退出码" : "Exit code"}: {detail.returnCode}</small> : null}
            </>
          ) : <div className="background-task-loading">{zh ? "读取日志…" : "Loading log…"}</div>}
        </div>
      ) : null}
    </li>
  ));

  return createPortal(
    <section className={"background-task-panel" + (expanded ? " is-expanded" : "")} role="dialog" aria-modal="false" aria-label={zh ? "后台任务" : "Background tasks"}>
      <div className="background-task-topbar">
        <div className="background-task-heading"><Terminal size={18} /><strong>{zh ? "后台任务" : "Background tasks"}</strong></div>
        <div className="background-task-actions">
          <button type="button" onClick={() => setExpanded(value => !value)} title={zh ? "切换大小" : "Toggle size"} aria-label={zh ? "切换大小" : "Toggle size"}><Maximize2 size={16} /></button>
          <button type="button" onClick={onClose} title={zh ? "关闭面板" : "Close panel"} aria-label={zh ? "关闭面板" : "Close panel"}><X size={18} /></button>
        </div>
      </div>
      {error ? <div className="background-task-notice"><CircleAlert size={14} />{error} <RefreshCw size={13} /></div> : null}
      <div className="background-task-scroll">
        <div className="background-task-group-label">
          {zh ? "运行中" : "Running"} <span>{running.length}</span>
        </div>
        {running.length ? <ul className="background-task-list">{list(running)}</ul> : <p className="background-task-empty">{zh ? "暂无运行中的命令" : "No running commands"}</p>}
        <button type="button" className="background-task-group-toggle" onClick={() => setShowFinished(value => !value)} aria-expanded={showFinished}>
          {zh ? "已结束" : "Finished"} <span>{finished.length}</span> {showFinished ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        </button>
        {showFinished && finished.length ? <ul className="background-task-list">{list(finished)}</ul> : null}
        {!connected ? <p className="background-task-empty">{zh ? "等待连接本地 Loom Host" : "Waiting for Loom Host"}</p> : null}
      </div>
    </section>,
    document.body,
  );
}
