import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from "react";
import { AttachmentQueue, MAX_ATTACHMENTS } from "./attachmentQueue";
import { AttachmentTray } from "./AttachmentTray";
import { MobileProjects, ProjectReadStatus } from "./MobileProjects";
import { MobileBuildPanel } from "./MobileBuildPanel";
import { searchThreads } from "./mobileProjectState";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  Blocks,
  Check,
  ChevronDown,
  ChevronLeft,
  CircleAlert,
  Ellipsis,
  FolderOpen,
  Laptop,
  MessagesSquare,
  Monitor,
  Plus,
  RotateCw,
  ShieldCheck,
  Square,
  WifiOff,
} from "../components/icons";
import { Transcript } from "../components/Transcript";
import { TranscriptScrollController } from "../components/TranscriptScrollController";
import { RETAIN } from "../motion/tokens";
import { useMotionPresence } from "../motion/useMotionPresence";
import { useLoom } from "../state/useLoom";
import type { AccountDevices, ApprovalDecision, ThreadRecord, TranscriptItem } from "../types/loom";
import {
  cancelPairing,
  confirmBiometric,
  fetchAccountDevices,
  onPairing,
  onShellState,
  pickMobileFiles,
  uploadMobileFile,
  readConnectionLog,
  readShellState,
  reportBusyThreads,
  selectDevice,
  startPairing,
  unpair,
  type ConnectionEvent,
  type PairingProgress,
  type ShellDevice,
  type ShellState,
} from "./mobileBridge";

type Loom = ReturnType<typeof useLoom>;
type Tab = "now" | "chat" | "build" | "projects" | "more";
type Space = "chat" | "build";
type CachedThread = { id: string; title: string; status: string; updatedAt: string };

const WEB_URL = "https://loom.smirel.com";
const THREAD_CACHE_KEY = "loom.mobile.threads";
const THREAD_CACHE_LIMIT = 30;
const ICON = { size: 20, strokeWidth: 1.75 } as const;
/** A blip is not news: the banner waits this long, and a reconnect usually beats it. */
const OFFLINE_NOTICE_MS = 3000;

const MODE_COPY: Record<string, { name: string; hint: string }> = {
  "read-only": { name: "只读", hint: "只看不改" },
  approval: { name: "需确认", hint: "每次操作都问你" },
  workspace: { name: "工作区", hint: "项目内自动执行，越界才问" },
  "full-access": { name: "完全访问", hint: "不再询问，电脑上的一切都可操作" },
};

const TABS: ReadonlyArray<{ key: Tab; label: string; icon: typeof Activity }> = [
  { key: "now", label: "现在", icon: Activity },
  { key: "chat", label: "对话", icon: MessagesSquare },
  { key: "build", label: "建造", icon: Blocks },
  { key: "projects", label: "项目", icon: FolderOpen },
  { key: "more", label: "更多", icon: Ellipsis },
];

function ago(value: unknown): string {
  const time = typeof value === "number" ? value * (value < 1e12 ? 1000 : 1) : Date.parse(String(value ?? ""));
  if (!Number.isFinite(time)) return "";
  const minutes = Math.floor(Math.max(0, Date.now() - time) / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} 小时前`;
  return `${Math.floor(minutes / 1440)} 天前`;
}

function threadBusy(thread: ThreadRecord): boolean {
  return thread.status === "running" || thread.status === "waiting_approval";
}

/** True once the value has stayed true for a while; false again at once. */
function useHeld(value: boolean, delayMs: number): boolean {
  const [held, setHeld] = useState(false);
  useEffect(() => {
    if (!value) { setHeld(false); return; }
    const timer = window.setTimeout(() => setHeld(true), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);
  return held;
}

function connectionEventText(entry: ConnectionEvent): string {
  const code = entry.detail.split(" ")[0];
  if (entry.event === "connected") return "已连接";
  if (entry.event === "computer-online") return "电脑上线";
  if (entry.event === "computer-offline") return "电脑离线";
  if (entry.event === "rest") return "切到后台较久，已暂停连接";
  if (entry.event === "refused") return "服务器拒绝了这次登录刷新";
  if (entry.event === "failed") return entry.detail === "401" || entry.detail === "403" ? "登录需要刷新" : "连不上服务器";
  if (code === "1012" || code === "1001") return "服务器更新，自动重连";
  if (code === "4401") return "登录需要刷新";
  if (code === "1013") return "网络太慢，连接被重置";
  return "连接中断";
}

function clockTime(at: number): string {
  return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function readCachedThreads(): CachedThread[] {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(THREAD_CACHE_KEY) || "[]") as unknown;
    return Array.isArray(parsed) ? parsed as CachedThread[] : [];
  } catch {
    return [];
  }
}

function writeCachedThreads(threads: ThreadRecord[]): void {
  const kept = threads.slice(0, THREAD_CACHE_LIMIT).map(({ id, title, status, updatedAt }) => ({ id, title, status, updatedAt }));
  try { window.localStorage.setItem(THREAD_CACHE_KEY, JSON.stringify(kept)); } catch {}
}

/** What it showed last, so a surface that is closing keeps its content for its exit. */
function useLatchedWhile<T>(open: boolean, value: T): T {
  const latched = useRef(value);
  if (open) latched.current = value;
  return latched.current;
}

function Sheet({ open, label, onClose, children }: { open: boolean; label: string; onClose(): void; children: ReactNode }) {
  const { mounted, phase } = useMotionPresence(open, RETAIN.flow);
  const content = useLatchedWhile(open, children);
  const panel = useRef<HTMLElement>(null);
  useLayoutEffect(() => {
    if (!open || !mounted) return;
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, [open, mounted]);
  useLayoutEffect(() => {
    if (!open) return;
    const back = (event: Event) => { event.preventDefault(); event.stopImmediatePropagation(); onClose(); };
    window.addEventListener("loom:mobile-back", back, true);
    return () => window.removeEventListener("loom:mobile-back", back, true);
  }, [open, onClose]);
  if (!mounted) return null;
  return (
    <div className="m-sheet-host" data-phase={phase} inert={!open}>
      <button type="button" className="m-scrim" aria-label="关闭" onClick={onClose} />
      <section ref={panel} tabIndex={-1} className="m-sheet" role="dialog" aria-modal="true" aria-label={label} onKeyDown={(event) => {
        if (event.key === "Escape") { event.preventDefault(); onClose(); }
        if (event.key === "Tab") {
          const nodes = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary') ?? []).filter((node) => node.getClientRects().length);
          const first = nodes[0], last = nodes[nodes.length - 1];
          if (!first) { event.preventDefault(); return; }
          if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
      }}>
        <span className="m-grip" aria-hidden="true" />
        <button type="button" className="m-sheet-close" onClick={onClose} aria-label={`关闭${label}`}>完成</button>
        {content}
      </section>
    </div>
  );
}

/** Makes its own room in the flow, and gives it back the same way. */
function Fold({ open, children }: { open: boolean; children: ReactNode }) {
  const { mounted, phase } = useMotionPresence(open, RETAIN.foldBase);
  const content = useLatchedWhile(open, children);
  if (!mounted) return null;
  return <div className="m-fold" data-phase={phase}><div>{content}</div></div>;
}

function Skeleton({ rows }: { rows: number }) {
  return <div className="m-skeleton" aria-hidden="true">{Array.from({ length: rows }, (_, index) => <i key={index} />)}</div>;
}

function Pair({ removed }: { removed: boolean }) {
  const [progress, setProgress] = useState<PairingProgress | null>(null);
  useEffect(() => onPairing(setProgress), []);
  const claimed = progress?.state === "claimed" ? progress : null;
  if (claimed) {
    return (
      <main className="m-pair is-confirming" key="confirm" aria-live="polite">
        <div className="m-pair-center">
          <p className="m-eyebrow">已识别「{claimed.deviceName || "这台电脑"}」</p>
          <strong className="m-confirm-code" aria-label={claimed.confirmCode}>
            {claimed.confirmCode.split("").map((digit, index) => <span aria-hidden="true" key={index}>{digit}</span>)}
          </strong>
          <h1>在电脑上点「允许」</h1>
          <p>先确认电脑显示的是同一个数字。</p>
          <p className="m-waiting"><RotateCw size={15} strokeWidth={1.75} spinning /> 等待电脑确认</p>
        </div>
        <button type="button" className="m-button" onClick={cancelPairing}>取消</button>
      </main>
    );
  }
  return (
    <main className="m-pair" key="start">
      <div className="m-pair-center">
        <img className="m-logo" src={`${import.meta.env.BASE_URL}loom-icon.png`} alt="" />
        <h1>连接你的电脑</h1>
        <p>{removed ? "这部手机已在电脑上被移除，重新配对后才能继续使用。" : "手机发消息，电脑照常工作。"}</p>
        <ol className="m-steps">
          <li>在电脑上打开 Loom</li>
          <li>进入「设置 › 设备」，点「添加手机」</li>
          <li>扫描电脑上的二维码</li>
        </ol>
      </div>
      <Fold open={progress?.state === "failed"}>
        <p className="m-alert" role="alert"><CircleAlert size={16} strokeWidth={1.75} />{progress?.state === "failed" ? progress.message : ""}</p>
      </Fold>
      <button type="button" className="m-button is-primary" onClick={startPairing}>扫描二维码</button>
    </main>
  );
}

function ThreadRow({ thread, detail, onOpen }: { thread: ThreadRecord; detail: string; onOpen(): void }) {
  const state = thread.status === "waiting_approval" ? "wait" : thread.status === "running" ? "run" : "idle";
  return (
    <button type="button" className="m-row" onClick={onOpen}>
      <span className={`m-dot is-${state}`} aria-hidden="true" />
      <span className="m-row-text"><strong>{thread.title || "新对话"}</strong></span>
      <small>{detail}</small>
    </button>
  );
}

function Group({ title, tone, children }: { title?: string; tone?: "wait" | "run"; children: ReactNode }) {
  return (
    <section className="m-group">
      {title ? <h2 className={tone ? `is-${tone}` : undefined}>{title}</h2> : null}
      <div className="m-rows">{children}</div>
    </section>
  );
}

function Empty({ icon, title, hint }: { icon: ReactNode; title: string; hint?: string }) {
  return <div className="m-empty"><span aria-hidden="true">{icon}</span><strong>{title}</strong>{hint ? <p>{hint}</p> : null}</div>;
}

function Now({ threads, onOpen }: { threads: ThreadRecord[]; onOpen(thread: ThreadRecord): void }) {
  const waiting = threads.filter((thread) => thread.status === "waiting_approval");
  const running = threads.filter((thread) => thread.status === "running");
  const recent = threads.filter((thread) => !threadBusy(thread)).slice(0, 8);
  if (!threads.length) return <Empty icon={<MessagesSquare size={24} strokeWidth={1.5} />} title="还没有对话" hint="发一个任务，电脑就会开始工作。" />;
  return (
    <>
      {waiting.length ? (
        <section className="m-group">
          <h2 className="is-wait">需要你 · {waiting.length}</h2>
          {waiting.map((thread) => (
            <button type="button" className="m-wait-card" key={thread.id} onClick={() => onOpen(thread)}>
              <i className="m-dot is-wait" aria-hidden="true" /><strong>{thread.title || "新对话"}</strong><span>查看并批准</span>
            </button>
          ))}
        </section>
      ) : null}
      {running.length ? (
        <Group title={`运行中 · ${running.length}`} tone="run">
          {running.map((thread) => <ThreadRow key={thread.id} thread={thread} detail={thread.weave ? "建造" : "工作中"} onOpen={() => onOpen(thread)} />)}
        </Group>
      ) : null}
      {recent.length ? <Group title="最近">{recent.map((thread) => <ThreadRow key={thread.id} thread={thread} detail={ago(thread.updatedAt)} onOpen={() => onOpen(thread)} />)}</Group> : null}
    </>
  );
}

function deviceIcon(device: ShellDevice) {
  return String(device.platform ?? "").startsWith("darwin") ? <Laptop {...ICON} /> : <Monitor {...ICON} />;
}

function Offline({ shell, cached }: { shell: ShellState; cached: CachedThread[] }) {
  const others = shell.devices.filter((device) => device.id !== shell.deviceId);
  return (
    <>
      <div className="m-offline">
        <span aria-hidden="true"><WifiOff size={22} strokeWidth={1.5} /></span>
        <h2>连不上{shell.deviceName || "这台电脑"}</h2>
        <p>它可能休眠、断网，或者退出了 Loom。任务现在的状态，要等它回来才能确认。</p>
        <button type="button" className="m-button" onClick={() => selectDevice(shell.deviceId)}>重新连接</button>
      </div>
      {others.length ? (
        <Group title="切换到其他电脑">
          {others.map((device) => (
            <button type="button" className="m-row" key={device.id} onClick={() => device.id && selectDevice(device.id)}>
              <span className="m-row-icon" aria-hidden="true">{deviceIcon(device)}</span>
              <span className="m-row-text"><strong>{device.name || "Loom 电脑"}</strong></span>
              <small>切换</small>
            </button>
          ))}
        </Group>
      ) : null}
      {cached.length ? (
        <Group title="离线前看到的">
          {cached.slice(0, 6).map((thread) => (
            <div className="m-row is-static" key={thread.id}>
              <span className="m-dot is-idle" aria-hidden="true" />
              <span className="m-row-text"><strong>{thread.title || "新对话"}</strong></span>
              <small>{ago(thread.updatedAt)}</small>
            </div>
          ))}
        </Group>
      ) : null}
    </>
  );
}

function Controls({ open, loom, online, onClose }: { open: boolean; loom: Loom; online: boolean; onClose(): void }) {
  const thread = loom.active?.thread;
  const mode = thread?.permissionMode || loom.draftPermissionMode || "approval";
  const modes = loom.runtime.permissionModes?.length ? loom.runtime.permissionModes : Object.keys(MODE_COPY);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const current = loom.models?.current;
  const reasoning = current?.reasoning;
  const reasoningValue = thread?.reasoning?.value ?? reasoning?.value;
  const attempt = (action: () => Promise<unknown>) => {
    if (busy || !online) return;
    setBusy(true);
    setError("");
    void action().catch((cause) => setError(cause instanceof Error ? cause.message : String(cause))).finally(() => setBusy(false));
  };
  const chooseMode = (next: string) => attempt(async () => {
    if (next === "full-access" && !(await confirmBiometric("切换到完全访问"))) return;
    await loom.setPermissionMode(next);
  });
  const profiles = (loom.models?.profiles ?? []).filter((profile) => profile.configured !== false && profile.accessState !== "denied" && profile.available !== false && !profile.setupOnly && `${profile.name} ${profile.model} ${profile.groupName || profile.adapter}`.toLowerCase().includes(query.toLowerCase()));
  return (
    <Sheet open={open} label="这个对话的权限与模型" onClose={onClose}>
      <h2 className="m-sheet-title">权限</h2>
      <div className="m-rows" role="radiogroup" aria-label="权限模式">
        {modes.map((value) => (
          <button type="button" className="m-choice" role="radio" aria-checked={value === mode} disabled={!online || busy} key={value} onClick={() => chooseMode(value)}>
            <span><strong>{MODE_COPY[value]?.name ?? value}</strong><small>{MODE_COPY[value]?.hint ?? ""}</small></span>
            {value === mode ? <Check size={18} strokeWidth={2} /> : value === "full-access" ? <em>需指纹</em> : null}
          </button>
        ))}
      </div>
      {reasoning?.options?.length ? (
        <>
          <h2 className="m-sheet-title">推理强度</h2>
          <div
            className="m-segments m-reasoning-options"
            role="radiogroup"
            aria-label="推理强度"
            style={{ "--m-count": reasoning.options.length, "--m-index": Math.max(0, reasoning.options.findIndex((option) => option.value === reasoningValue)) } as CSSProperties}
          >
            <i className="m-glider" aria-hidden="true" />
            {reasoning.options.map((option) => (
              <button type="button" role="radio" disabled={!online || busy} aria-checked={option.value === reasoningValue} key={option.value} onClick={() => attempt(() => loom.setReasoning(reasoning.kind, option.value))}>
                {option.label ?? option.value}
              </button>
            ))}
          </div>
        </>
      ) : null}
      <h2 className="m-sheet-title">模型</h2>
      <label className="m-field">搜索模型<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="模型名或服务商" /></label>
      <button type="button" className="m-inline-button" disabled={!online || busy || loom.modelBusy} onClick={() => attempt(() => loom.refreshModels(true))}>刷新可用模型</button>
      <div className="m-rows" role="radiogroup" aria-label="模型">
        {profiles.map((profile) => (
          <button type="button" className="m-choice" role="radio" aria-checked={profile.selection === current?.selection} disabled={!online || busy || loom.modelBusy} key={profile.selection} onClick={() => attempt(() => loom.switchModelProfile(profile.selection))}>
            <span><strong>{profile.name || profile.model}</strong><small>{profile.groupName || profile.adapter} · {profile.model}</small></span>
            {profile.selection === current?.selection ? <Check size={18} strokeWidth={2} /> : null}
          </button>
        ))}
      </div>
      {!profiles.length ? <p className="m-note">{query ? "没有匹配的模型" : "暂无可用模型，请在电脑端完成模型配置后刷新。"}</p> : null}
      <Fold open={Boolean(error)}><p className="m-alert" role="alert"><CircleAlert size={16} strokeWidth={1.75} />{error}</p></Fold>
    </Sheet>
  );
}

function ThreadActions({ open, loom, online, running, onClose, onLeave }: { open: boolean; loom: Loom; online: boolean; running: boolean; onClose(): void; onLeave(): void }) {
  const thread = loom.active?.thread;
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { if (open) { setTitle(thread?.title || ""); setError(""); } }, [open, thread?.id]);
  const act = async (action: () => Promise<unknown>) => {
    if (busy || !online) return;
    setBusy(true); setError("");
    try { await action(); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };
  return <Sheet open={open} label="对话详情与操作" onClose={onClose}>
    <h2 className="m-sheet-title">对话详情</h2>
    <p className="m-path">{thread?.workspace || "发送第一条消息后创建对话"}</p>
    {thread ? <>
      <label className="m-field">对话标题<input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} disabled={busy} /></label>
      <button type="button" className="m-button" disabled={!online || busy || !title.trim() || title.trim() === thread.title} onClick={() => void act(() => loom.renameThread(thread.id, title.trim()))}>保存标题</button>
      <h2 className="m-sheet-title">运行与上下文</h2>
      <div className="m-actions">{running ? <button type="button" className="m-button" disabled={!online || busy} onClick={() => void act(() => loom.setPaused(!thread.paused))}>{thread.paused ? "继续对话运行" : "在安全点暂停"}</button> : null}<button type="button" className="m-button" disabled={!online || busy} onClick={() => void act(() => loom.refreshActiveThreadState())}>刷新对话状态</button></div>
      <p className="m-note">{loom.context ? `上下文约 ${loom.context.usedTokens.toLocaleString()} token${loom.context.usedPercent != null ? ` · ${Math.round(loom.context.usedPercent)}%` : ""}` : "上下文用量尚未读取"}</p>
      <div className="m-actions"><button type="button" className="m-button" disabled={!online || busy} onClick={() => void act(() => loom.refreshContext(thread.id))}>刷新用量</button><button type="button" className="m-button" disabled={!online || busy || running || loom.compacting} onClick={() => void act(() => loom.compactContext())}>{loom.compacting ? "正在整理上下文…" : "整理上下文"}</button></div>
      <p className="m-note">整理上下文会调用模型生成摘要，保留近期内容。</p>
      <h2 className="m-sheet-title">对话管理</h2>
      <div className="m-actions"><button type="button" className="m-button" disabled={!online || busy || running} onClick={() => void act(async () => { await loom.forkThread(thread.id); onClose(); })}>从这里分支新对话</button><button type="button" className="m-button" disabled={!online || busy || running} onClick={() => void act(async () => { await loom.archiveThread(thread.id, !thread.archived, () => undefined); onClose(); onLeave(); })}>{thread.archived ? "恢复到对话列表" : "归档对话"}</button></div>
    </> : <p className="m-note">模型和权限可从输入框旁调整。</p>}
    {error ? <p className="m-alert" role="alert">{error}</p> : null}
  </Sheet>;
}

function ProjectChooser({ open, loom, online, onChoose, onClose }: { open: boolean; loom: Loom; online: boolean; onChoose(workspace?: string, projectId?: string): void; onClose(): void }) {
  useEffect(() => { if (open && online) void loom.refreshProjects(); }, [open, online, loom.refreshProjects]);
  return (
    <Sheet open={open} label="选择项目" onClose={onClose}>
      <h2 className="m-sheet-title">在哪个项目里开始？</h2>
      <ProjectReadStatus loom={loom} online={online} />
      {!loom.projects.length && !loom.projectsLoading && !loom.projectsError ? <p className="m-note">还没有项目。可在「项目」页添加电脑上的工作目录。</p> : null}
      <div className="m-rows">
        {loom.projects.map((project) => (
          <button type="button" className="m-choice" key={project.id} disabled={!online} onClick={() => onChoose(project.root, project.id)}>
            <span className="m-row-icon" aria-hidden="true"><FolderOpen {...ICON} /></span>
            <span><strong>{project.name}</strong><small>{project.root}</small></span>
          </button>
        ))}
        <button type="button" className="m-choice" disabled={!online} onClick={() => onChoose()}>
          <span className="m-row-icon" aria-hidden="true"><Monitor {...ICON} /></span>
          <span><strong>不选项目</strong><small>使用电脑上的默认位置</small></span>
        </button>
      </div>
    </Sheet>
  );
}

/** The approval card lives in the transcript; this brings you back when you have scrolled off it. */
function useApprovalOffscreen(active: boolean): boolean {
  const [offscreen, setOffscreen] = useState(false);
  useEffect(() => {
    setOffscreen(false);
    if (!active) return;
    let observer: IntersectionObserver | null = null;
    const attach = window.setInterval(() => {
      const card = document.querySelector(".m-stage .approval-card");
      if (!card) return;
      window.clearInterval(attach);
      observer = new IntersectionObserver(([entry]) => setOffscreen(!entry.isIntersecting));
      observer.observe(card);
    }, 200);
    return () => { window.clearInterval(attach); observer?.disconnect(); };
  }, [active]);
  return offscreen;
}

function ThreadView({ loom, space, online, connecting, draftWorkspace, onBack, onMode, onOpenAgent }: { loom: Loom; space: Space; online: boolean; connecting: boolean; draftWorkspace?: string; onBack(): void; onMode(mode: Space): void; onOpenAgent(id: string): void }) {
  const thread = loom.active?.thread;
  // The screen arrives at once; until the Host answers it must not show the previous conversation.
  const opening = Boolean(loom.openingThreadId && thread?.id !== loom.openingThreadId);
  const offlineNotice = useHeld(!online, OFFLINE_NOTICE_MS);
  const running = Boolean(loom.turnActive || (thread && threadBusy(thread)));
  const [text, setText] = useState("");
  const [pane, setPane] = useState<"chat" | "build">(space === "build" ? "build" : "chat");
  const [weaveBudget, setWeaveBudget] = useState(1000000);
  const [buildBusy, setBuildBusy] = useState(false);
  const [details, setDetails] = useState(false);
  const [queue] = useState(() => new AttachmentQueue(uploadMobileFile));
  const attachments = useSyncExternalStore(queue.subscribe, queue.snapshot);
  const [picking, setPicking] = useState(false);
  const [sending, setSending] = useState(false);
  const sendLock = useRef(false);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; queue.dispose(); }, [queue]);
  const attachmentsReady = attachments.every((item) => item.status === "ready");
  const [controls, setControls] = useState(false);
  const [error, setError] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
  const approvalOffscreen = useApprovalOffscreen(Boolean(loom.active?.pendingApproval));
  const approvalJump = useMotionPresence(approvalOffscreen, RETAIN.fast);
  const fieldHeight = useRef(0);
  const fail = (cause: unknown) => setError(cause instanceof Error ? cause.message : String(cause));

  const handleApproval = useCallback(async (item: TranscriptItem, decision: ApprovalDecision, feedback?: string) => {
    setError("");
    try {
      if (decision === "acceptWithPrefixRule" && !(await confirmBiometric("这个项目以后都允许这类命令"))) return;
      await loom.respondApproval(item, decision, feedback);
    } catch (cause) { fail(cause); }
  }, [loom.respondApproval]);

  const submit = async () => {
    const body = text.trim();
    if (sendLock.current || buildBusy || !online || picking || !attachmentsReady || thread?.archived || (!body && !attachments.length)) return;
    const sent = attachments.map((item) => ({ path: item.path!, name: item.file.name }));
    sendLock.current = true;
    setSending(true); setError("");
    try {
      await loom.send(body, sent, undefined, { weave: space === "build", ...(space === "build" ? { weaveBudget } : {}) });
      if (alive.current) { setText(""); queue.clear(); }
    } catch (cause) {
      if (alive.current) fail(cause);
    } finally { sendLock.current = false; if (alive.current) setSending(false); }
  };

  const attach = async () => {
    if (picking || sending || buildBusy || !online) return;
    setPicking(true);
    setError("");
    try {
      const files = await pickMobileFiles();
      if (alive.current) setError(queue.add(files).join("\n"));
    } catch (cause) { if (alive.current) fail(cause); }
    finally { if (alive.current) setPicking(false); }
  };

  // Measured at auto height with the transition off, then played from the old height to the new one.
  useLayoutEffect(() => {
    const area = field.current;
    if (!area) return;
    area.style.transition = "none";
    area.style.height = "auto";
    const next = Math.min(area.scrollHeight, 132);
    area.style.height = `${fieldHeight.current || next}px`;
    void area.offsetHeight;
    area.style.transition = "";
    area.style.height = `${next}px`;
    fieldHeight.current = next;
  }, [text]);

  const mode = thread?.permissionMode || loom.draftPermissionMode || "approval";
  const blank = !thread && !loom.items.length && !opening;
  return (
    <div className="m-thread">
      <Fold open={offlineNotice}><p className="m-banner" role="status">{connecting ? "正在重新连接…" : "电脑离线，暂时不能操作"}</p></Fold>
      <header className="m-thread-head">
        <button type="button" className="m-icon-button" aria-label="返回" onClick={onBack}><ChevronLeft size={24} strokeWidth={1.75} /></button>
        <div>
          <h1 key={thread?.title ?? space}>{opening ? "正在打开…" : thread?.title || (space === "build" ? "新建造" : "新任务")}</h1>
          <p><span className={`m-dot is-${!online ? "idle" : running ? "run" : "on"}`} aria-hidden="true" />{!online ? "电脑离线" : running ? "正在运行" : "在线"}</p>
        </div>
        <button type="button" className="m-icon-button" aria-label="对话详情与操作" disabled={opening || sending || buildBusy} onClick={() => setDetails(true)}><Ellipsis size={22} /></button>
      </header>
      <div className="m-thread-switch" role="group" aria-label="对话与建造模式">
        <button type="button" aria-pressed={space === "chat"} disabled={sending || buildBusy || opening} onClick={() => { onMode("chat"); setPane("chat"); }}>对话模式</button>
        <button type="button" aria-pressed={space === "build" && pane === "build"} disabled={sending || buildBusy || opening || thread?.archived} onClick={() => { onMode("build"); setPane("build"); }}>建造面板</button>
        {space === "build" ? <button type="button" aria-pressed={pane === "chat"} onClick={() => setPane("chat")}>查看对话{loom.active?.pendingApproval ? " · 待审批" : ""}</button> : null}
      </div>
      {space === "build" && !opening ? <div className="m-stage m-build-stage" hidden={pane !== "build"}><MobileBuildPanel key={thread?.id || "draft"} threadId={thread?.id} workspace={thread?.workspace || draftWorkspace} online={online} chatting={running} visible={pane === "build"} attachments={attachments.filter((item) => item.status === "ready").map((item) => ({ path: item.path!, name: item.file.name }))} attachmentsReady={attachmentsReady && !picking && !sending} onConsumeAttachments={() => queue.clear()} onBudget={setWeaveBudget} onBusy={setBuildBusy} onOpenAgent={onOpenAgent} /></div> : null}
      {opening ? (
        <div className="m-stage is-opening"><Skeleton rows={4} /></div>
      ) : blank ? (
        <div className="m-stage is-blank" hidden={space === "build" && pane === "build"}>
          <Empty icon={space === "build" ? <Blocks size={24} strokeWidth={1.5} /> : <MessagesSquare size={24} strokeWidth={1.5} />} title={space === "build" ? "想让电脑建造什么？" : "想让电脑做什么？"} hint="工作在电脑上进行，你可以随时回来看。" />
        </div>
      ) : (
        <div className="conversation-stage m-stage" hidden={space === "build" && pane === "build"}>
          <Transcript
            threadId={thread?.id}
            runtimePhase={loom.modelActivity?.turnId === thread?.currentTurnId ? loom.modelActivity?.phase : undefined}
            items={loom.items}
            running={running}
            currentTurnId={running ? thread?.currentTurnId : undefined}
            workspace={thread?.workspace}
            onApproval={handleApproval}
            usageReady={false}
          />
          <TranscriptScrollController
            items={loom.items}
            threadId={thread?.id}
            currentTurnId={thread?.currentTurnId}
            running={running}
            hasOlder={loom.hasOlderTurns}
            loadingOlder={loom.loadingOlderTurns}
            onLoadOlder={loom.loadOlderTurns}
          />
          {approvalJump.mounted ? (
            <button type="button" className="m-approval-jump" data-phase={approvalJump.phase} onClick={() => document.querySelector(".m-stage .approval-card")?.scrollIntoView({ block: "center" })}>
              <ArrowDown size={16} strokeWidth={2} />等你批准
            </button>
          ) : null}
        </div>
      )}
      <footer className="m-composer">
        {space === "build" ? <p className="m-attachment-hint">建造模式 · 消息会发送给建造对话，启动方案需在面板确认</p> : null}
        {thread?.archived ? <p className="m-alert">此对话已归档，可在详情中恢复后继续。</p> : null}
        <Fold open={Boolean(error)}><p className="m-alert" role="alert"><CircleAlert size={16} strokeWidth={1.75} />{error}</p></Fold>
        <AttachmentTray items={attachments} queue={queue} online={online} disabled={sending || buildBusy} />
        {attachments.length > 0 ? <p className="m-attachment-hint">图片和文件 · 单个最大 10 MB</p> : null}
        {attachments.some((item) => item.status === "error") ? <p className="m-attachment-hint" role="status">请重试或移除失败的附件后再发送</p> : null}
        <div className="m-composer-box">
          <textarea ref={field} rows={1} disabled={sending || buildBusy || thread?.archived} aria-label={running ? "插话" : "发消息"} placeholder={running ? "插话，下个安全点生效" : space === "build" ? "描述建造目标，或向电脑提问" : "发消息"} value={text} onChange={(event) => setText(event.target.value)} onPaste={(event) => {
            const files = Array.from(event.clipboardData.files);
            if (files.length && online && !sending && !buildBusy) { event.preventDefault(); setError(queue.add(files).join("\n")); }
          }} />
          <div className="m-composer-tools">
            <button type="button" className="m-tool" aria-label={picking ? "正在选择附件" : "添加附件"} title="添加文件或图片 · 单个最大 10 MB，最多 10 个" disabled={!online || picking || sending || buildBusy || thread?.archived || attachments.length >= MAX_ATTACHMENTS} onClick={() => void attach()}><Plus {...ICON} /></button>
            <button type="button" className="m-chip" disabled={sending || buildBusy || opening} onClick={() => setControls(true)}>
              <ShieldCheck size={15} strokeWidth={1.75} />
              <span key={`${mode}:${loom.models?.current?.selection ?? ""}`}>{MODE_COPY[mode]?.name ?? mode} · {loom.models?.current?.name || loom.models?.current?.model || "模型"}</span>
              <ChevronDown size={14} strokeWidth={1.75} />
            </button>
            {running && !text.trim() && !attachments.length ? (
              <button type="button" key="stop" className="m-round is-stop" aria-label="停止运行" disabled={!online} onClick={() => void loom.interrupt().catch(fail)}><Square size={14} strokeWidth={2} fill="currentColor" /></button>
            ) : (
              <button type="button" key="send" className="m-round is-send" aria-label={sending ? "正在发送" : "发送"} disabled={!online || sending || picking || buildBusy || thread?.archived || !attachmentsReady || (!text.trim() && !attachments.length)} onClick={() => void submit()}>{sending ? <i className="m-attachment-spinner" /> : <ArrowUp size={19} strokeWidth={2.25} />}</button>
            )}
          </div>
        </div>
      </footer>
      <Controls open={controls} loom={loom} online={online} onClose={() => setControls(false)} />
      <ThreadActions open={details} loom={loom} online={online} running={running} onClose={() => setDetails(false)} onLeave={onBack} />
    </div>
  );
}

function ConnectionLog({ open, onClose }: { open: boolean; onClose(): void }) {
  const events = useMemo(() => (open ? readConnectionLog() : []), [open]);
  return (
    <Sheet open={open} label="连接记录" onClose={onClose}>
      <h2 className="m-sheet-title">连接记录</h2>
      {events.length ? (
        <div className="m-rows">
          {events.map((entry) => (
            <div className="m-row is-static" key={`${entry.at}:${entry.event}`}>
              <span className="m-row-text"><strong>{connectionEventText(entry)}</strong></span>
              <small>{clockTime(entry.at)}</small>
            </div>
          ))}
        </div>
      ) : <p className="m-note">还没有记录。</p>}
    </Sheet>
  );
}

function More({ shell }: { shell: ShellState }) {
  const [confirming, setConfirming] = useState(false);
  const [devices, setDevices] = useState<AccountDevices | null>(null);
  const [log, setLog] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    fetchAccountDevices()
      .then((next) => { if (live) setDevices(next); })
      .catch((cause) => { if (live) setError(cause instanceof Error ? cause.message : String(cause)); });
    return () => { live = false; };
  }, [shell.socket]);

  // The relay knows who is online right now; the account remembers everyone else.
  const liveIds = new Set(shell.devices.map((device) => device.id));
  const known = devices?.computers ?? [];
  const computers = [
    ...shell.devices.map((device) => ({ id: device.id ?? "", name: device.name ?? "", platform: device.platform ?? "", online: true, seen: 0 })),
    ...known.filter((computer) => !liveIds.has(computer.id)).map((computer) => ({ id: computer.id, name: computer.name, platform: computer.platform, online: false, seen: computer.last_seen_at })),
  ];

  return (
    <>
      <Group title="电脑">
        {computers.length ? computers.map((device) => (
          <button type="button" className="m-row" key={device.id} disabled={device.id === shell.deviceId || !device.online} onClick={() => selectDevice(device.id)}>
            <span className={`m-row-icon${device.online ? "" : " is-dim"}`} aria-hidden="true">{deviceIcon(device)}</span>
            <span className="m-row-text"><strong>{device.name || "Loom 电脑"}</strong></span>
            {device.id === shell.deviceId ? <Check size={18} strokeWidth={2} /> : <small>{device.online ? "切换" : `最近在线 ${ago(device.seen)}`}</small>}
          </button>
        )) : <p className="m-rows-note">没有在线的电脑。</p>}
      </Group>
      {devices?.phones.length ? (
        <Group title="受信任的手机">
          {devices.phones.map((phone) => (
            <div className="m-row is-static" key={phone.id}>
              <span className={`m-dot is-${phone.current || phone.online ? "on" : "idle"}`} aria-hidden="true" />
              <span className="m-row-text"><strong>{phone.name || "手机"}</strong></span>
              <small>{phone.current ? "这部手机" : phone.online ? "在线" : `最近使用 ${ago(phone.last_used_at)}`}</small>
            </div>
          ))}
        </Group>
      ) : null}
      {devices?.history.length ? (
        <Group title="历史设备">
          {devices.history.map((phone) => (
            <div className="m-row is-static" key={phone.id}>
              <span className="m-row-text"><strong>{phone.name || "手机"}</strong></span>
              <small>{ago(phone.removed_at)}移除</small>
            </div>
          ))}
        </Group>
      ) : null}
      <Fold open={Boolean(error)}><p className="m-alert" role="alert"><CircleAlert size={16} strokeWidth={1.75} />{error}</p></Fold>
      <Group>
        <button type="button" className="m-row" onClick={() => setLog(true)}>
          <span className="m-row-text"><strong>连接记录</strong></span>
          <small>查看掉线原因</small>
        </button>
        <button type="button" className="m-row" onClick={() => void window.loom.openExternal(WEB_URL)}>
          <span className="m-row-text"><strong>在网页打开完整版</strong></span>
          <small>loom.smirel.com</small>
        </button>
      </Group>
      <Group title="这部手机">
        {confirming ? (
          <div className="m-confirm" key="confirm">
            <p>解除后这部手机不能再控制电脑，需要重新扫码配对。</p>
            <div>
              <button type="button" className="m-button" onClick={() => setConfirming(false)}>取消</button>
              <button type="button" className="m-button is-danger" onClick={unpair}>解除配对</button>
            </div>
          </div>
        ) : (
          <button type="button" className="m-row is-danger" onClick={() => setConfirming(true)}><span className="m-row-text"><strong>解除配对</strong></span></button>
        )}
      </Group>
      <p className="m-note">移除其他手机、修改模型密钥和账号，都要在电脑上操作。</p>
      <ConnectionLog open={log} onClose={() => setLog(false)} />
    </>
  );
}

function Session({ shell, onRestart }: { shell: ShellState; onRestart(): void }) {
  const loom = useLoom();
  const [tab, setTab] = useState<Tab>("now");
  const [open, setOpen] = useState<Space | null>(null);
  const [viewKey, setViewKey] = useState(0);
  const [query, setQuery] = useState("");
  const [navigationError, setNavigationError] = useState("");
  const [draftWorkspace, setDraftWorkspace] = useState("");
  const [spaces, setSpaces] = useState<Record<string, Space>>(() => {
    try { const stored = JSON.parse(localStorage.getItem(`loom.mobile.spaces:${shell.deviceId}`) || "{}"); return Object.fromEntries(Object.entries(stored).filter(([, mode]) => mode === "chat" || mode === "build")) as Record<string, Space>; } catch { return {}; }
  });
  const [choosing, setChoosing] = useState<Space | null>(null);
  const cached = useMemo(readCachedThreads, []);
  const online = shell.socket === "open" && shell.online;
  const offlineNotice = useHeld(!online, OFFLINE_NOTICE_MS);
  const screen = useMotionPresence(Boolean(open), RETAIN.flow);
  const space = useLatchedWhile(Boolean(open), open ?? "chat");
  const chosenSpace = useLatchedWhile(Boolean(choosing), choosing ?? "chat");

  const threads = useMemo(() => [...loom.threads].filter((thread) => !thread.parentThreadId)
    .sort((a, b) => Date.parse(String(b.updatedAt)) - Date.parse(String(a.updatedAt))), [loom.threads]);
  const modeOf = (thread: ThreadRecord): Space => spaces[thread.id] || (thread.weave ? "build" : "chat");
  useEffect(() => {
    const id = loom.active?.thread.id;
    if (open && id && !loom.openingThreadId) setSpaces((current) => current[id] === open ? current : { ...current, [id]: open });
  }, [open, loom.active?.thread.id, loom.openingThreadId]);
  useEffect(() => { try { localStorage.setItem(`loom.mobile.spaces:${shell.deviceId}`, JSON.stringify(spaces)); } catch {} }, [spaces, shell.deviceId]);
  useEffect(() => {
    if (!online || loom.connection !== "ready") return;
    const refresh = () => { if (!document.hidden) { void loom.refreshProjects(); void loom.refreshThreads().catch(() => {}); } };
    const timer = window.setInterval(refresh, 30000);
    window.addEventListener("focus", refresh); document.addEventListener("visibilitychange", refresh);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, [online, loom.connection, loom.refreshProjects, loom.refreshThreads]);

  // The shell keeps the connection for these after the app leaves the screen, so an
  // approval on a turn that began before the app opened still reaches the phone.
  useEffect(() => {
    if (loom.connection !== "ready") return;
    reportBusyThreads(loom.threads.filter(threadBusy).map((thread) => thread.id));
    writeCachedThreads(threads);
  }, [loom.connection, loom.threads, threads]);

  // Startup gave up while the computer was away and has nothing to reconcile: run it again.
  useEffect(() => { if (online && loom.connection === "error") onRestart(); }, [loom.connection, onRestart, online]);

  const openThread = (thread: ThreadRecord) => {
    setViewKey((key) => key + 1);
    setOpen(modeOf(thread));
    setNavigationError("");
    void loom.openThread(thread.id).catch((cause) => setNavigationError(cause instanceof Error ? cause.message : String(cause)));
  };

  const begin = (space: Space, workspace?: string, projectId?: string) => {
    setViewKey((key) => key + 1);
    setChoosing(null);
    setOpen(space);
    setDraftWorkspace(workspace || "");
    setNavigationError("");
    void loom.newThread(workspace, projectId);
  };

  useEffect(() => {
    if (!open) return;
    // The system Back key: cancelling the event tells the shell it was handled.
    const back = (event: Event) => {
      if (event.defaultPrevented || document.querySelector('.m-sheet-host:not([inert]), .user-message-image-lightbox:not([inert])')) return;
      event.preventDefault(); setOpen(null);
    };
    window.addEventListener("loom:mobile-back", back);
    return () => window.removeEventListener("loom:mobile-back", back);
  }, [open]);

  const list = searchThreads(threads.filter((thread) => modeOf(thread) === (tab === "build" ? "build" : "chat")), query);
  const showOffline = offlineNotice && !threads.length && tab !== "more";
  const loading = online && loom.connection === "connecting" && !threads.length;
  const title = tab === "now" ? shell.deviceName || "Loom" : TABS.find((entry) => entry.key === tab)?.label;
  return (
    <div className="m-app">
      <div className="m-shell" data-covered={open ? "" : undefined} inert={Boolean(open)}>
        <header className="m-head">
          <h1 key={title}>{title}</h1>
          <p><span className={`m-dot is-${online ? "on" : shell.socket === "connecting" ? "run" : "idle"}`} aria-hidden="true" />{online ? "在线" : shell.socket === "connecting" ? "连接中" : "离线"}</p>
        </header>
        <Fold open={offlineNotice && threads.length > 0}><p className="m-banner" role="status">{shell.socket === "connecting" ? "正在重新连接…" : "电脑离线，下面是离线前的内容"}</p></Fold>
        <main className="m-body" key={tab}>
          {navigationError ? <p className="m-alert" role="alert">{navigationError}</p> : null}
          {showOffline ? <Offline shell={shell} cached={cached} /> : null}
          {loading ? <Skeleton rows={5} /> : null}
          {!showOffline && !loading && tab === "now" ? <Now threads={threads} onOpen={openThread} /> : null}
          {!showOffline && !loading && (tab === "chat" || tab === "build") ? (
            <><div className="m-library-tools"><label className="m-field">搜索{tab === "build" ? "建造" : "对话"}<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="标题或工作目录" /></label><div className="m-actions"><button type="button" className="m-inline-button" disabled={!online} onClick={() => void loom.refreshThreads().catch((cause) => setNavigationError(String(cause)))}>刷新列表</button><button type="button" className="m-inline-button" disabled={!online} aria-pressed={loom.threadView === "archived"} onClick={() => void loom.setThreadView(loom.threadView === "archived" ? "active" : "archived").catch((cause) => setNavigationError(String(cause)))}>{loom.threadView === "archived" ? "返回未归档" : "查看已归档"}</button></div></div>{list.length ? (
              <Group>{list.map((thread) => <ThreadRow key={thread.id} thread={thread} detail={thread.status === "waiting_approval" ? "等你批准" : thread.status === "running" ? "运行中" : ago(thread.updatedAt)} onOpen={() => openThread(thread)} />)}</Group>
            ) : (
              <Empty icon={tab === "build" ? <Blocks size={24} strokeWidth={1.5} /> : <MessagesSquare size={24} strokeWidth={1.5} />} title={query ? "没有匹配的对话" : loom.threadView === "archived" ? "没有已归档的对话" : tab === "build" ? "还没有建造" : "还没有对话"} />
            )}</>
          ) : null}
          {tab === "projects" ? <MobileProjects loom={loom} online={online} threads={threads} onOpen={openThread} onBegin={(mode, project) => begin(mode, project.root, project.id)} /> : null}
          {tab === "more" ? <More shell={shell} /> : null}
        </main>
        <Fold open={tab !== "more" && tab !== "projects" && online}>
          <div className="m-new">
            <button type="button" onClick={() => setChoosing(tab === "build" ? "build" : "chat")}>
              <span key={tab}>{tab === "build" ? "开始一个建造" : `给${shell.deviceName || "电脑"}一个新任务`}</span>
              <i aria-hidden="true"><Plus size={18} strokeWidth={2.25} /></i>
            </button>
          </div>
        </Fold>
        <nav className="m-nav" aria-label="主导航" style={{ "--m-index": TABS.findIndex((entry) => entry.key === tab) } as CSSProperties}>
          <i className="m-glider" aria-hidden="true" />
          {TABS.map(({ key, label, icon: Icon }) => (
            <button type="button" key={key} aria-current={tab === key ? "page" : undefined} onClick={() => { setTab(key); setQuery(""); if ((key === "now" || key === "projects") && loom.threadView === "archived") void loom.setThreadView("active").catch((cause) => setNavigationError(String(cause))); }}>
              <i aria-hidden="true"><Icon {...ICON} /></i>{label}
            </button>
          ))}
        </nav>
      </div>
      {screen.mounted ? (
        <div className="m-screen" data-phase={screen.phase}>
          <ThreadView key={viewKey} loom={loom} space={space} online={online} draftWorkspace={draftWorkspace} connecting={shell.socket === "connecting"} onBack={() => setOpen(null)} onMode={setOpen} onOpenAgent={(id) => { setViewKey((key) => key + 1); setOpen("chat"); void loom.openThread(id).catch((cause) => setNavigationError(String(cause))); }} />
        </div>
      ) : null}
      <ProjectChooser open={Boolean(choosing)} loom={loom} online={online} onClose={() => setChoosing(null)} onChoose={(workspace, projectId) => begin(chosenSpace, workspace, projectId)} />
    </div>
  );
}

export function MobileApp() {
  const [shell, setShell] = useState<ShellState>(readShellState);
  const [session, setSession] = useState(0);
  const wasPaired = useRef(shell.paired);
  const restart = useCallback(() => setSession((value) => value + 1), []);
  useEffect(() => onShellState(setShell), []);
  useEffect(() => {
    if (shell.paired && !wasPaired.current) restart();
    wasPaired.current = shell.paired;
  }, [restart, shell.paired]);
  useEffect(() => { document.documentElement.dataset.loomTheme = "dark"; }, []);
  if (!shell.paired) return <Pair removed={Boolean(shell.removed)} />;
  return <Session key={`${session}:${shell.deviceId}`} shell={shell} onRestart={restart} />;
}
