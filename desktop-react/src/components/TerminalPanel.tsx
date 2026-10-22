import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Terminal as XTerminal, type ITheme } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon } from "@xterm/addon-search";
import {
  ArrowDown,
  ChevronDown,
  ChevronUp,
  Copy,
  Download,
  Maximize2,
  Minimize2,
  Plus,
  RotateCcw,
  Search,
  Square,
  Terminal,
  Trash2,
  X,
} from "./icons";
import { PanelHeader } from "./PanelHeader";
import { createTerminalInputPump } from "../terminalInput";
import { useI18n } from "../i18n";
import {
  type TerminalSession,
  type TerminalRead,
  type TerminalRequest,
} from "../terminal";
import "@xterm/xterm/css/xterm.css";
import "./terminal-panel.css";

type Profile = { id: string; label: string };
type PaneHandle = {
  term: XTerminal;
  search: SearchAddon;
  fit(): void;
  wake(): void;
};
const message = (error: unknown) =>
  error instanceof Error ? error.message : String(error);

function terminalTheme(): ITheme {
  const light = document.documentElement.dataset.loomTheme === "light";
  return {
    background: light ? "#f8f9fb" : "#101216",
    foreground: light ? "#303843" : "#d3d8e0",
    cursor: light ? "#6152cc" : "#b0a4ff",
    selectionBackground: light ? "#c9c1f080" : "#8170ce60",
    black: "#353b45",
    red: "#e06c75",
    green: "#86ba83",
    yellow: "#d8b970",
    blue: "#719bd8",
    magenta: "#b99add",
    cyan: "#67b8c7",
    white: "#c4cbd4",
    brightBlack: "#7c8795",
    brightRed: "#f28b94",
    brightGreen: "#a3d59e",
    brightYellow: "#edcd8b",
    brightBlue: "#9bbef0",
    brightMagenta: "#d2b4ee",
    brightCyan: "#91d5df",
    brightWhite: "#f4f6fa",
  };
}

function TerminalPane({
  session,
  visible,
  connected,
  fontSize,
  onReady,
  onStatus,
  onError,
  onSearch,
}: {
  session: TerminalSession;
  visible: boolean;
  connected: boolean;
  fontSize: number;
  onReady(id: string, handle: PaneHandle | null): void;
  onStatus(session: TerminalSession): void;
  onError(error: string): void;
  onSearch(): void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const handle = useRef<PaneHandle | null>(null);
  const live = useRef({
    visible,
    connected,
    running: session.running,
    onStatus,
    onError,
    onSearch,
  });
  live.current = {
    visible,
    connected,
    running: session.running,
    onStatus,
    onError,
    onSearch,
  };
  useEffect(() => {
    if (!host.current) return;
    const term = new XTerminal({
      cursorBlink: true,
      scrollback: 10000,
      fontSize,
      fontFamily:
        getComputedStyle(document.documentElement)
          .getPropertyValue("--loom-code-font")
          .trim() || "Consolas, monospace",
      theme: terminalTheme(),
      allowProposedApi: false,
      screenReaderMode: true,
      allowTransparency: false,
    });
    const fit = new FitAddon();
    const search = new SearchAddon();
    term.loadAddon(fit);
    term.loadAddon(search);
    term.open(host.current);
    let disposed = false;
    let timer = 0;
    let resizeTimer = 0;
    let cursor = 0;
    let exitPolls = 0;
    let retries = 0;
    let reading = false;
    let readRequested = false;
    let resizeKey = "";
    const params = {
      workspace: session.workspace,
      processId: session.processId,
    };
    const wake = () => {
      if (disposed || exitPolls >= 3) return;
      clearTimeout(timer);
      if (reading) readRequested = true;
      else timer = window.setTimeout(poll, 0);
    };
    const input = createTerminalInputPump({
      send: (text) =>
        window.loom.call("terminal/session/write", { ...params, text }),
      writable: () =>
        !disposed && live.current.connected && live.current.running,
      activity: wake,
      error: (error) => live.current.onError(message(error)),
    });
    const fitTerminal = () => {
      if (!live.current.visible || !host.current?.clientWidth || disposed)
        return;
      fit.fit();
      const key = `${term.rows}:${term.cols}`;
      if (key === resizeKey || !live.current.connected || !live.current.running)
        return;
      clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => {
        resizeKey = key;
        void window.loom
          .call("terminal/session/resize", {
            ...params,
            rows: term.rows,
            cols: term.cols,
          })
          .catch((error) => {
            if (!disposed && live.current.running)
              live.current.onError(message(error));
          });
      }, 100);
    };
    const controller: PaneHandle = { term, search, fit: fitTerminal, wake };
    handle.current = controller;
    onReady(session.processId, controller);
    const data = term.onData(input.write);
    term.attachCustomKeyEventHandler((event) => {
      if (event.type !== "keydown") return true;
      if (
        (event.ctrlKey || event.metaKey) &&
        event.shiftKey &&
        event.code === "KeyC"
      ) {
        void navigator.clipboard
          .writeText(term.getSelection())
          .catch((error) => live.current.onError(message(error)));
        return false;
      }
      if (
        (event.ctrlKey || event.metaKey) &&
        event.shiftKey &&
        event.code === "KeyV"
      )
        return false;
      if ((event.ctrlKey || event.metaKey) && event.code === "KeyF") {
        live.current.onSearch();
        return false;
      }
      return true;
    });
    const observer = new ResizeObserver(fitTerminal);
    observer.observe(host.current);
    const themes = new MutationObserver(() => {
      term.options.theme = terminalTheme();
    });
    themes.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-loom-theme"],
    });
    async function poll() {
      if (disposed) return;
      if (reading) {
        readRequested = true;
        return;
      }
      if (!live.current.connected) {
        timer = window.setTimeout(poll, 800);
        return;
      }
      reading = true;
      readRequested = false;
      let outputReceived = false;
      try {
        const result = await window.loom.call<TerminalRead>(
          "terminal/session/read",
          { ...params, cursor },
        );
        if (disposed) return;
        if (result.reset) {
          term.reset();
          cursor = 0;
        }
        if (result.truncated)
          term.writeln("\r\n[Earlier output is no longer available]\r\n");
        // Replayed ConPTY output can contain cursor-position queries. An
        // exited command cannot receive the emulator's automatic responses.
        live.current.running = result.running;
        term.options.disableStdin = !result.running;
        term.options.cursorBlink = result.running;
        if (result.output) {
          term.write(result.output);
          outputReceived = true;
        }
        cursor = result.cursor;
        retries = 0;
        live.current.onStatus(result);
        if (!result.running) {
          exitPolls += 1;
          if (exitPolls >= 3) {
            term.writeln(
              `\r\n[${result.failure || `Process exited · ${result.returnCode ?? result.state}`}]`,
            );
            return;
          }
        }
      } catch (error) {
        if (disposed) return;
        retries += 1;
        if (retries === 1) live.current.onError(message(error));
      } finally {
        reading = false;
      }
      if (!disposed)
        timer = window.setTimeout(
          poll,
          retries
            ? Math.min(5000, 500 * retries)
            : readRequested
              ? 0
              : live.current.visible
                ? outputReceived
                  ? 16
                  : 32
                : 500,
        );
    }
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
      clearTimeout(resizeTimer);
      input.dispose();
      observer.disconnect();
      themes.disconnect();
      data.dispose();
      onReady(session.processId, null);
      handle.current = null;
      term.dispose();
    };
  }, [session.processId, session.workspace]);
  useEffect(() => {
    if (!handle.current) return;
    handle.current.term.options.fontSize = fontSize;
    if (visible) {
      handle.current.fit();
      handle.current.wake();
      handle.current.term.focus();
    }
  }, [visible, fontSize, connected]);
  return (
    <div
      className="terminal-emulator"
      ref={host}
      hidden={!visible}
      role="tabpanel"
      id={`terminal-pane-${session.processId}`}
      aria-labelledby={`terminal-tab-${session.processId}`}
    />
  );
}

export default function TerminalPanel({
  workspace,
  connected,
  open,
  request,
  onClose,
}: {
  workspace: string;
  connected: boolean;
  open: boolean;
  request: TerminalRequest | null;
  onClose(): void;
}) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [sessions, setSessions] = useState<TerminalSession[]>([]);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [shell, setShell] = useState("");
  const [cwd, setCwd] = useState(".");
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [found, setFound] = useState(true);
  const [renaming, setRenaming] = useState("");
  const [fontSize, setFontSize] = useState(13);
  const searchInput = useRef<HTMLInputElement>(null);
  const handles = useRef(new Map<string, PaneHandle>());
  const generation = useRef(0);
  const operation = useRef(false);
  const handled = useRef<TerminalRequest | null>(null);
  const active = sessions.find((s) => s.processId === selected);
  const ready = useCallback((id: string, pane: PaneHandle | null) => {
    if (pane) handles.current.set(id, pane);
    else handles.current.delete(id);
  }, []);
  const status = useCallback((next: TerminalSession) => {
    setSessions((current) => {
      const previous = current.find((s) => s.processId === next.processId);
      if (
        !previous ||
        (previous.running === next.running &&
          previous.state === next.state &&
          previous.returnCode === next.returnCode &&
          previous.failure === next.failure)
      )
        return current;
      return current.map((s) =>
        s.processId === next.processId &&
        (s.running !== next.running ||
          s.state !== next.state ||
          s.returnCode !== next.returnCode ||
          s.failure !== next.failure)
          ? {
              ...s,
              running: next.running,
              state: next.state,
              returnCode: next.returnCode,
              failure: next.failure,
            }
          : s,
      );
    });
  }, []);
  const showSearch = useCallback(() => {
    setSearchOpen(true);
    requestAnimationFrame(() => searchInput.current?.focus());
  }, []);
  useEffect(() => {
    const epoch = ++generation.current;
    setSessions([]);
    setSelected("");
    setLoading(true);
    setError("");
    setCwd(".");
    if (!workspace || !connected) {
      setLoading(false);
      return;
    }
    void Promise.all([
      window.loom.call<{ sessions: TerminalSession[] }>(
        "terminal/session/list",
        { workspace },
      ),
      window.loom.call<{ profiles: Profile[] }>("terminal/profiles", {}),
    ])
      .then(([result, available]) => {
        if (generation.current !== epoch) return;
        setSessions(result.sessions || []);
        setSelected(result.sessions?.[0]?.processId || "");
        setProfiles(available.profiles || []);
        setShell((current) =>
          available.profiles?.some((p) => p.id === current)
            ? current
            : available.profiles?.[0]?.id || "",
        );
      })
      .catch((error) => {
        if (generation.current === epoch) setError(message(error));
      })
      .finally(() => {
        if (generation.current === epoch) setLoading(false);
      });
    return () => {
      generation.current += 1;
    };
  }, [workspace, connected]);
  const create = async (code?: TerminalRequest) => {
    if (operation.current || !workspace || !connected) return;
    operation.current = true;
    setBusy(true);
    setError("");
    const epoch = generation.current;
    try {
      const session = await window.loom.call<TerminalSession>(
        "terminal/session/create",
        {
          workspace,
          shell,
          cwd: code?.command ? "." : cwd,
          command: code?.command,
          language: code?.language,
          title: code?.command
            ? `${code.language || "Shell"} · ${code.command.split("\n")[0].slice(0, 35)}`
            : undefined,
        },
      );
      if (generation.current !== epoch) return;
      setSessions((current) => [...current, session]);
      setSelected(session.processId);
    } catch (error) {
      if (generation.current === epoch) setError(message(error));
    } finally {
      operation.current = false;
      setBusy(false);
    }
  };
  useEffect(() => {
    if (
      !request ||
      handled.current === request ||
      loading ||
      busy ||
      request.workspace !== workspace
    )
      return;
    handled.current = request;
    if (request.processId) {
      const epoch = generation.current;
      setSelected(request.processId);
      if (!sessions.some((s) => s.processId === request.processId)) {
        void window.loom
          .call<{ sessions: TerminalSession[] }>("terminal/session/list", {
            workspace,
          })
          .then((result) => {
            if (generation.current === epoch)
              setSessions(result.sessions || []);
          })
          .catch((e) => {
            if (generation.current === epoch) setError(message(e));
          });
      }
    } else if (request.command) void create(request);
  }, [request, loading, busy, workspace]);
  useEffect(() => {
    if (open)
      requestAnimationFrame(() => handles.current.get(selected)?.term.focus());
  }, [open, selected]);
  const closeSession = async (session: TerminalSession) => {
    setError("");
    try {
      await window.loom.call("terminal/session/close", {
        workspace: session.workspace,
        processId: session.processId,
      });
      setSessions((current) => {
        const next = current.filter((s) => s.processId !== session.processId);
        setSelected((id) =>
          id === session.processId ? next.at(-1)?.processId || "" : id,
        );
        return next;
      });
    } catch (error) {
      setError(message(error));
    }
  };
  const search = (reverse = false) => {
    const addon = handles.current.get(selected)?.search;
    setFound(
      !query ||
        Boolean(reverse ? addon?.findPrevious(query) : addon?.findNext(query)),
    );
  };
  const download = () => {
    const term = handles.current.get(selected)?.term;
    if (!term) return;
    const text = Array.from(
      { length: term.buffer.active.length },
      (_, i) => term.buffer.active.getLine(i)?.translateToString(true) || "",
    ).join("\n");
    const url = URL.createObjectURL(
      new Blob([text], { type: "text/plain;charset=utf-8" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "loom-terminal.log";
    a.click();
    URL.revokeObjectURL(url);
  };
  const rename = async () => {
    if (!active || !renaming.trim()) {
      setRenaming("");
      return;
    }
    try {
      const result = await window.loom.call<TerminalSession>(
        "terminal/session/rename",
        { workspace, processId: active.processId, title: renaming },
      );
      setSessions((current) =>
        current.map((s) => (s.processId === result.processId ? result : s)),
      );
      setRenaming("");
    } catch (error) {
      setError(message(error));
    }
  };
  return createPortal(
    <aside
      className={`terminal-panel pl-float ${expanded ? "is-expanded" : ""}`}
      hidden={!open}
      aria-label={zh ? "终端" : "Terminal"}
      onKeyDown={(e) => {
        if (e.key === "Escape" && searchOpen) {
          setSearchOpen(false);
          handles.current.get(selected)?.term.focus();
          e.stopPropagation();
        }
      }}
    >
      <PanelHeader
        title={zh ? "终端" : "Terminal"}
        mark={<Terminal size={17} />}
        subtitle={workspace}
        subtitleCode
        subtitleHint={workspace}
        closeLabel={
          zh
            ? "收起终端（会话继续运行）"
            : "Hide terminal (sessions keep running)"
        }
        onClose={onClose}
        actions={
          <>
            <button
              className="pl-icon-button"
              title={zh ? "搜索 · Ctrl F" : "Find · Ctrl F"}
              aria-label={zh ? "搜索终端" : "Find in terminal"}
              onClick={showSearch}
              disabled={!active}
            >
              <Search size={15} />
            </button>
            <button
              className="pl-icon-button"
              title={zh ? "导出输出" : "Export output"}
              aria-label={zh ? "导出输出" : "Export output"}
              onClick={download}
              disabled={!active}
            >
              <Download size={15} />
            </button>
            <button
              className="pl-icon-button"
              title={zh ? "清屏" : "Clear screen"}
              aria-label={zh ? "清屏" : "Clear screen"}
              onClick={() => handles.current.get(selected)?.term.clear()}
              disabled={!active}
            >
              <Trash2 size={15} />
            </button>
            <button
              className="pl-icon-button"
              title={zh ? "最大化 / 还原" : "Maximize / restore"}
              aria-label={zh ? "最大化终端" : "Maximize terminal"}
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
            </button>
          </>
        }
      />
      <div className="terminal-toolbar">
        <select
          aria-label={zh ? "Shell 类型" : "Shell profile"}
          value={shell}
          onChange={(e) => setShell(e.target.value)}
          disabled={busy || loading}
        >
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
        <input
          className="terminal-cwd"
          aria-label={zh ? "新终端工作目录" : "New terminal directory"}
          value={cwd}
          onChange={(e) => setCwd(e.target.value)}
          title={
            zh
              ? "相对于项目的工作目录；也可在终端中使用 cd"
              : "Directory relative to the project; use cd inside the terminal"
          }
        />
        <button
          type="button"
          className="terminal-new"
          onClick={() => void create()}
          disabled={busy || loading || !connected || !shell}
        >
          <Plus size={14} />
          {busy
            ? zh
              ? "正在启动…"
              : "Starting…"
            : zh
              ? "新建终端"
              : "New terminal"}
        </button>
      </div>
      {sessions.length > 0 && (
        <div
          className="terminal-tabs"
          role="tablist"
          aria-label={zh ? "终端会话" : "Terminal sessions"}
        >
          {sessions.map((session) => (
            <div
              className={`terminal-tab ${selected === session.processId ? "is-active" : ""}`}
              key={session.processId}
            >
              <button
                id={`terminal-tab-${session.processId}`}
                role="tab"
                aria-selected={selected === session.processId}
                aria-controls={`terminal-pane-${session.processId}`}
                tabIndex={selected === session.processId ? 0 : -1}
                onClick={() => setSelected(session.processId)}
                onDoubleClick={() => {
                  setSelected(session.processId);
                  setRenaming(session.title);
                }}
                onKeyDown={(e) => {
                  if (
                    !["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)
                  )
                    return;
                  e.preventDefault();
                  const index = sessions.indexOf(session);
                  const next =
                    sessions[
                      e.key === "Home"
                        ? 0
                        : e.key === "End"
                          ? sessions.length - 1
                          : (index +
                              (e.key === "ArrowRight" ? 1 : -1) +
                              sessions.length) %
                            sessions.length
                    ];
                  setSelected(next.processId);
                  document
                    .getElementById(`terminal-tab-${next.processId}`)
                    ?.focus();
                }}
                title={`${session.cwd}\n${session.command || session.shell}`}
              >
                <span
                  className={`terminal-state-dot ${session.running ? "is-running" : ""}`}
                />
                <span>{session.title}</span>
                {!session.running && (
                  <small>{session.returnCode ?? session.state}</small>
                )}
              </button>
              <button
                type="button"
                className="terminal-tab-close"
                aria-label={`${zh ? "关闭并结束终端" : "Close and terminate terminal"}: ${session.title}`}
                onClick={() => void closeSession(session)}
              >
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
      {renaming && (
        <form
          className="terminal-search"
          onSubmit={(e) => {
            e.preventDefault();
            void rename();
          }}
        >
          <input
            aria-label={zh ? "会话名称" : "Session name"}
            value={renaming}
            onChange={(e) => setRenaming(e.target.value)}
            autoFocus
            maxLength={80}
          />
          <button type="submit">{zh ? "保存" : "Save"}</button>
          <button type="button" onClick={() => setRenaming("")}>
            <X size={14} />
          </button>
        </form>
      )}
      {searchOpen && (
        <div className="terminal-search">
          <Search size={14} />
          <input
            ref={searchInput}
            aria-label={zh ? "查找终端输出" : "Search terminal output"}
            placeholder={zh ? "查找输出…" : "Find output…"}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setFound(true);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") search(e.shiftKey);
            }}
          />
          <button aria-label="Previous match" onClick={() => search(true)}>
            <ChevronUp size={14} />
          </button>
          <button aria-label="Next match" onClick={() => search()}>
            <ChevronDown size={14} />
          </button>
          {!found && <small>{zh ? "无匹配" : "No results"}</small>}
          <button
            aria-label="Close search"
            onClick={() => setSearchOpen(false)}
          >
            <X size={14} />
          </button>
        </div>
      )}
      {error && (
        <div className="terminal-notice" role="alert">
          <span>{error}</span>
          <button aria-label="Dismiss error" onClick={() => setError("")}>
            <X size={13} />
          </button>
        </div>
      )}
      {!connected && (
        <div className="terminal-notice" role="status">
          {zh
            ? "连接已断开，重连后继续接收输出。"
            : "Disconnected. Output resumes after reconnecting."}
        </div>
      )}
      <div className="terminal-content">
        {loading ? (
          <div className="terminal-empty">
            {zh ? "正在载入终端…" : "Loading terminals…"}
          </div>
        ) : !sessions.length ? (
          <div className="terminal-empty">
            <Terminal size={30} strokeWidth={1.3} />
            <strong>
              {zh ? "项目中的命令行" : "Your project, at the command line"}
            </strong>
            <p>
              {zh
                ? "新建终端以运行命令、启动服务或调试。收起面板后，会话仍会继续运行。"
                : "Run commands, start a server or debug. Sessions keep running when you hide this panel."}
            </p>
            <button
              onClick={() => void create()}
              disabled={busy || !connected || !shell}
            >
              <Plus size={15} />
              {zh ? "新建终端" : "New terminal"}
            </button>
          </div>
        ) : (
          sessions.map((session) => (
            <TerminalPane
              key={session.processId}
              session={session}
              visible={open && selected === session.processId}
              connected={connected}
              fontSize={fontSize}
              onReady={ready}
              onStatus={status}
              onError={setError}
              onSearch={showSearch}
            />
          ))
        )}
      </div>
      <footer className="terminal-footer">
        <span className={active?.running ? "is-running" : ""}>
          {active
            ? active.running
              ? zh
                ? "运行中"
                : "Running"
              : `${zh ? "已结束" : "Exited"} · ${active.returnCode ?? active.state}`
            : `${sessions.length} ${zh ? "个会话" : "sessions"}`}
        </span>
        <span className="terminal-footer-actions">
          <button
            title="Copy selection · Ctrl Shift C"
            aria-label="Copy selection"
            onClick={() =>
              void navigator.clipboard
                .writeText(
                  handles.current.get(selected)?.term.getSelection() || "",
                )
                .catch((e) => setError(message(e)))
            }
            disabled={!active}
          >
            <Copy size={13} />
          </button>
          <button
            title="Scroll to bottom"
            aria-label="Scroll to bottom"
            onClick={() => handles.current.get(selected)?.term.scrollToBottom()}
            disabled={!active}
          >
            <ArrowDown size={13} />
          </button>
          <button
            title="Interrupt · Ctrl C"
            aria-label="Interrupt command"
            onClick={() =>
              void window.loom
                .call("terminal/session/write", {
                  workspace,
                  processId: selected,
                  text: "\u0003",
                })
                .catch((e) => setError(message(e)))
            }
            disabled={!active?.running || !connected}
          >
            <Square size={12} />
          </button>
          <button
            title="Rename session"
            aria-label="Rename session"
            onClick={() => setRenaming(active?.title || "")}
            disabled={!active}
          >
            {zh ? "重命名" : "Rename"}
          </button>
          {active && !active.running && (
            <button
              onClick={() =>
                void create(
                  active.command
                    ? {
                        workspace,
                        command: active.command,
                        language: active.language,
                      }
                    : undefined,
                )
              }
              disabled={busy || !connected}
            >
              <RotateCcw size={13} />
              {zh ? "重新运行" : "Rerun"}
            </button>
          )}
          <select
            aria-label="Terminal font size"
            value={fontSize}
            onChange={(e) => setFontSize(Number(e.target.value))}
          >
            {[11, 12, 13, 14, 16, 18, 20].map((n) => (
              <option key={n} value={n}>
                {n}px
              </option>
            ))}
          </select>
        </span>
      </footer>
    </aside>,
    document.body,
  );
}
