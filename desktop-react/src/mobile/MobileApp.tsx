import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Transcript } from "../components/Transcript";
import { TranscriptScrollController } from "../components/TranscriptScrollController";
import { useLoom } from "../state/useLoom";
import type { ApprovalDecision, ThreadRecord, TranscriptItem } from "../types/loom";
import {
  cancelPairing,
  confirmBiometric,
  onPairing,
  onShellState,
  readShellState,
  reportBusyThreads,
  selectDevice,
  startPairing,
  unpair,
  type PairingProgress,
  type ShellState,
} from "./mobileBridge";

type Loom = ReturnType<typeof useLoom>;
type Tab = "now" | "chat" | "build" | "more";

const MODE_COPY: Record<string, { name: string; hint: string }> = {
  "read-only": { name: "只读", hint: "只看不改" },
  approval: { name: "需确认", hint: "每次操作都问你" },
  workspace: { name: "工作区", hint: "项目内自动执行，越界才问" },
  "full-access": { name: "完全访问", hint: "不再询问，电脑上的一切都可操作" },
};

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

function Sheet({ label, onClose, children }: { label: string; onClose(): void; children: ReactNode }) {
  return (
    <div className="m-sheet-host">
      <button type="button" className="m-scrim" aria-label="关闭" onClick={onClose} />
      <section className="m-sheet" role="dialog" aria-modal="true" aria-label={label}>
        <span className="m-grip" aria-hidden="true" />
        {children}
      </section>
    </div>
  );
}

function Pair() {
  const [progress, setProgress] = useState<PairingProgress | null>(null);
  useEffect(() => onPairing(setProgress), []);
  const claimed = progress?.state === "claimed" ? progress : null;
  return (
    <main className="m-pair">
      <h1>连接你的电脑</h1>
      <p>在电脑上打开 Loom，进入「设置 › 手机」，点「添加手机」，然后扫描屏幕上的二维码。</p>
      {claimed ? (
        <section className="m-card" aria-live="polite">
          <h2>已识别「{claimed.deviceName || "这台电脑"}」</h2>
          <p>确认电脑上显示同样的数字，然后在电脑上点「允许」。</p>
          <strong className="m-confirm-code">{claimed.confirmCode.slice(0, 3)} {claimed.confirmCode.slice(3)}</strong>
          <button type="button" className="m-button" onClick={cancelPairing}>取消</button>
        </section>
      ) : (
        <button type="button" className="m-button is-primary" onClick={startPairing}>扫描二维码</button>
      )}
      {progress?.state === "failed" ? <p className="m-error" role="alert">{progress.message}</p> : null}
    </main>
  );
}

function ThreadRow({ thread, detail, onOpen }: { thread: ThreadRecord; detail: string; onOpen(): void }) {
  return (
    <button type="button" className="m-row" onClick={onOpen}>
      <span className={`m-dot is-${thread.status === "waiting_approval" ? "wait" : thread.status === "running" ? "run" : "idle"}`} aria-hidden="true" />
      <span className="m-row-text"><strong>{thread.title || "新对话"}</strong><small>{detail}</small></span>
    </button>
  );
}

function Now({ threads, onOpen }: { threads: ThreadRecord[]; onOpen(thread: ThreadRecord): void }) {
  const waiting = threads.filter((thread) => thread.status === "waiting_approval");
  const running = threads.filter((thread) => thread.status === "running");
  const recent = threads.filter((thread) => !threadBusy(thread)).slice(0, 8);
  return (
    <>
      {waiting.length ? (
        <section className="m-group">
          <h2 className="is-wait">需要你 · {waiting.length}</h2>
          {waiting.map((thread) => (
            <button type="button" className="m-wait-card" key={thread.id} onClick={() => onOpen(thread)}>
              <strong>{thread.title || "新对话"}</strong><span>查看并批准</span>
            </button>
          ))}
        </section>
      ) : null}
      {running.length ? (
        <section className="m-group">
          <h2 className="is-run">运行中 · {running.length}</h2>
          {running.map((thread) => <ThreadRow key={thread.id} thread={thread} detail={thread.weave ? "建造" : "正在工作"} onOpen={() => onOpen(thread)} />)}
        </section>
      ) : null}
      <section className="m-group">
        <h2>最近</h2>
        {recent.length ? recent.map((thread) => <ThreadRow key={thread.id} thread={thread} detail={ago(thread.updatedAt)} onOpen={() => onOpen(thread)} />) : <p className="m-empty">还没有对话。</p>}
      </section>
    </>
  );
}

function Controls({ loom, onClose }: { loom: Loom; onClose(): void }) {
  const thread = loom.active?.thread;
  const mode = thread?.permissionMode || loom.draftPermissionMode || "approval";
  const modes = loom.runtime.permissionModes?.length ? loom.runtime.permissionModes : Object.keys(MODE_COPY);
  const [error, setError] = useState("");
  const current = loom.models?.current;
  const reasoning = current?.reasoning;
  const attempt = (action: () => Promise<unknown>) => { setError(""); void action().catch((cause) => setError(cause instanceof Error ? cause.message : String(cause))); };
  const chooseMode = (next: string) => attempt(async () => {
    if (next === "full-access" && !(await confirmBiometric("切换到完全访问"))) return;
    await loom.setPermissionMode(next);
  });
  const profiles = (loom.models?.profiles ?? []).filter((profile) => profile.configured !== false && profile.accessState !== "denied");
  return (
    <Sheet label="这个对话的权限与模型" onClose={onClose}>
      <h2 className="m-sheet-title">这个对话的权限</h2>
      <div className="m-options" role="radiogroup" aria-label="权限模式">
        {modes.map((value) => (
          <label className="m-option" key={value}>
            <input type="radio" name="mode" checked={value === mode} onChange={() => chooseMode(value)} />
            <span><strong>{MODE_COPY[value]?.name ?? value}</strong><small>{MODE_COPY[value]?.hint ?? ""}</small></span>
            {value === "full-access" ? <em>需指纹</em> : null}
          </label>
        ))}
      </div>
      <h2 className="m-sheet-title">模型</h2>
      <select className="m-select" aria-label="模型" value={current?.selection ?? ""} disabled={loom.modelBusy} onChange={(event) => attempt(() => loom.switchModelProfile(event.target.value))}>
        {profiles.map((profile) => <option key={profile.selection} value={profile.selection}>{profile.name || profile.model}</option>)}
      </select>
      {reasoning?.options?.length ? (
        <>
          <h2 className="m-sheet-title">推理强度</h2>
          <select className="m-select" aria-label="推理强度" value={thread?.reasoning?.value ?? reasoning.value} onChange={(event) => attempt(() => loom.setReasoning(reasoning.kind, event.target.value))}>
            {reasoning.options.map((option) => <option key={option.value} value={option.value}>{option.label ?? option.value}</option>)}
          </select>
        </>
      ) : null}
      {error ? <p className="m-error" role="alert">{error}</p> : null}
    </Sheet>
  );
}

function ProjectChooser({ loom, onChoose, onClose }: { loom: Loom; onChoose(workspace?: string, projectId?: string): void; onClose(): void }) {
  return (
    <Sheet label="选择项目" onClose={onClose}>
      <h2 className="m-sheet-title">在哪个项目里开始？</h2>
      <div className="m-options">
        {loom.projects.map((project) => (
          <button type="button" className="m-option is-button" key={project.id} onClick={() => onChoose(project.root, project.id)}>
            <span><strong>{project.name}</strong><small>{project.root}</small></span>
          </button>
        ))}
        <button type="button" className="m-option is-button" onClick={() => onChoose()}><span><strong>不选项目</strong><small>使用电脑上的默认位置</small></span></button>
      </div>
    </Sheet>
  );
}

function ThreadView({ loom, space, online, onBack }: { loom: Loom; space: "chat" | "build"; online: boolean; onBack(): void }) {
  const thread = loom.active?.thread;
  const running = Boolean(loom.turnActive || (thread && threadBusy(thread)));
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<{ path: string; name: string }[]>([]);
  const [controls, setControls] = useState(false);
  const [error, setError] = useState("");
  const field = useRef<HTMLTextAreaElement>(null);
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
    if (!body && !attachments.length) return;
    const sent = attachments;
    setError(""); setText(""); setAttachments([]);
    try {
      await loom.send(body, sent, undefined, { weave: space === "build" });
    } catch (cause) {
      setText(body); setAttachments(sent); fail(cause);
    }
  };

  const attach = async () => {
    setError("");
    try {
      const paths = await window.loom.pickFiles();
      setAttachments((current) => [...current, ...paths.map((path) => ({ path, name: path.split(/[\\/]/).pop() || "附件" }))]);
    } catch (cause) { fail(cause); }
  };

  useEffect(() => {
    const area = field.current;
    if (!area) return;
    area.style.height = "auto";
    area.style.height = `${Math.min(area.scrollHeight, 132)}px`;
  }, [text]);

  const mode = thread?.permissionMode || loom.draftPermissionMode || "approval";
  return (
    <div className="m-thread">
      <header className="m-thread-head">
        <button type="button" className="m-icon-button" aria-label="返回" onClick={onBack}>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m15 6-6 6 6 6" /></svg>
        </button>
        <div><h1>{thread?.title || (space === "build" ? "新建造" : "新任务")}</h1><p>{running ? "正在运行" : online ? "在线" : "电脑离线"}</p></div>
      </header>
      <div className="conversation-stage m-stage">
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
      </div>
      <footer className="m-composer">
        {error ? <p className="m-error" role="alert">{error}</p> : null}
        <button type="button" className="m-chip" onClick={() => setControls(true)}>{MODE_COPY[mode]?.name ?? mode} · {loom.models?.current?.name || loom.models?.current?.model || "模型"}</button>
        {attachments.length ? <p className="m-attachments">{attachments.map((file) => file.name).join("、")}</p> : null}
        <div className="m-composer-row">
          <button type="button" className="m-round" aria-label="添加附件" disabled={!online} onClick={() => void attach()}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
          </button>
          <textarea ref={field} rows={1} aria-label={running ? "插话" : "发消息"} placeholder={running ? "插话，下个安全点生效" : "发消息"} value={text} onChange={(event) => setText(event.target.value)} />
          {running && !text.trim() ? (
            <button type="button" className="m-round is-stop" aria-label="停止运行" disabled={!online} onClick={() => void loom.interrupt().catch(fail)}><span /></button>
          ) : (
            <button type="button" className="m-round is-send" aria-label="发送" disabled={!online || (!text.trim() && !attachments.length)} onClick={() => void submit()}>
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 19V5M6 11l6-6 6 6" /></svg>
            </button>
          )}
        </div>
      </footer>
      {controls ? <Controls loom={loom} onClose={() => setControls(false)} /> : null}
    </div>
  );
}

function More({ shell }: { shell: ShellState }) {
  const [confirming, setConfirming] = useState(false);
  return (
    <>
      <section className="m-group">
        <h2>电脑</h2>
        {shell.devices.length ? shell.devices.map((device) => (
          <button type="button" className="m-row" key={device.id} disabled={device.id === shell.deviceId} onClick={() => device.id && selectDevice(device.id)}>
            <span className={`m-dot is-${device.id === shell.deviceId ? "run" : "idle"}`} aria-hidden="true" />
            <span className="m-row-text"><strong>{device.name || "Loom 电脑"}</strong><small>{device.id === shell.deviceId ? "正在使用" : "切换到这台"}</small></span>
          </button>
        )) : <p className="m-empty">没有在线的电脑。</p>}
      </section>
      <section className="m-group">
        <h2>这部手机</h2>
        {confirming ? (
          <div className="m-card">
            <p>解除后这部手机不能再控制电脑，需要重新扫码配对。</p>
            <div className="m-pair-actions">
              <button type="button" className="m-button" onClick={() => setConfirming(false)}>取消</button>
              <button type="button" className="m-button is-danger" onClick={unpair}>解除配对</button>
            </div>
          </div>
        ) : <button type="button" className="m-button" onClick={() => setConfirming(true)}>解除配对</button>}
      </section>
      <p className="m-empty">模型密钥和账号只能在电脑上修改。</p>
    </>
  );
}

const LAST_THREAD_KEY = "loom.mobile.lastThread";

function Session({ shell }: { shell: ShellState }) {
  const loom = useLoom();
  const [tab, setTab] = useState<Tab>("now");
  const [open, setOpen] = useState<"chat" | "build" | null>(null);
  const [choosing, setChoosing] = useState<"chat" | "build" | null>(null);
  const online = shell.socket === "open" && shell.online;

  const threads = useMemo(() => [...loom.threads].filter((thread) => !thread.parentThreadId && !thread.archived)
    .sort((a, b) => Date.parse(String(b.updatedAt)) - Date.parse(String(a.updatedAt))), [loom.threads]);

  // The shell keeps the connection for these after the app leaves the screen, so an
  // approval on a turn that began before the app opened still reaches the phone.
  useEffect(() => {
    if (loom.connection === "ready") reportBusyThreads(loom.threads.filter(threadBusy).map((thread) => thread.id));
  }, [loom.connection, loom.threads]);

  const openThread = (thread: ThreadRecord) => {
    setOpen(thread.weave ? "build" : "chat");
    try { window.sessionStorage.setItem(LAST_THREAD_KEY, thread.id); } catch {}
    void loom.openThread(thread.id);
  };

  const begin = (space: "chat" | "build", workspace?: string, projectId?: string) => {
    setChoosing(null);
    setOpen(space);
    void loom.newThread(workspace, projectId);
  };

  useEffect(() => {
    if (!open) return;
    // The system Back key: cancelling the event tells the shell it was handled.
    const back = (event: Event) => { event.preventDefault(); setOpen(null); };
    window.addEventListener("loom:mobile-back", back);
    return () => window.removeEventListener("loom:mobile-back", back);
  }, [open]);

  const banner = !online ? <p className="m-banner" role="status">{shell.socket === "connecting" ? "正在重新连接…" : `${shell.deviceName || "电脑"}离线，下面是离线前看到的内容，暂时不能操作`}</p> : null;

  if (open) return <>{banner}<ThreadView loom={loom} space={open} online={online} onBack={() => setOpen(null)} /></>;

  const list = tab === "chat" ? threads.filter((thread) => !thread.weave) : threads.filter((thread) => thread.weave);
  return (
    <div className="m-shell">
      <header className="m-head">
        <div><h1>{tab === "now" ? shell.deviceName || "Loom" : tab === "chat" ? "对话" : tab === "build" ? "建造" : "更多"}</h1>
          <p><span className={`m-dot is-${online ? "on" : "idle"}`} aria-hidden="true" />{online ? "在线" : shell.socket === "connecting" ? "连接中" : "离线"}</p></div>
      </header>
      {banner}
      {loom.connection === "error" && online ? <p className="m-banner" role="alert">{loom.error}</p> : null}
      <main className="m-body">
        {loom.connection === "connecting" && !threads.length ? <p className="m-empty">正在读取电脑上的对话…</p> : null}
        {tab === "now" ? <Now threads={threads} onOpen={openThread} /> : null}
        {tab === "chat" || tab === "build" ? (
          <section className="m-group">
            {list.length ? list.map((thread) => <ThreadRow key={thread.id} thread={thread} detail={threadBusy(thread) ? "运行中" : ago(thread.updatedAt)} onOpen={() => openThread(thread)} />) : <p className="m-empty">还没有{tab === "build" ? "建造" : "对话"}。</p>}
          </section>
        ) : null}
        {tab === "more" ? <More shell={shell} /> : null}
      </main>
      {tab !== "more" ? (
        <div className="m-new">
          <button type="button" disabled={!online} onClick={() => setChoosing(tab === "build" ? "build" : "chat")}>
            <span>{tab === "build" ? "开始一个建造" : `给${shell.deviceName || "电脑"}一个新任务`}</span>
            <i aria-hidden="true"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M12 5v14M5 12h14" /></svg></i>
          </button>
        </div>
      ) : null}
      <nav className="m-nav" aria-label="主导航">
        {([["now", "现在"], ["chat", "对话"], ["build", "建造"], ["more", "更多"]] as const).map(([key, label]) => (
          <button type="button" key={key} aria-current={tab === key ? "page" : undefined} onClick={() => setTab(key)}>{label}</button>
        ))}
      </nav>
      {choosing ? <ProjectChooser loom={loom} onClose={() => setChoosing(null)} onChoose={(workspace, projectId) => begin(choosing, workspace, projectId)} /> : null}
    </div>
  );
}

export function MobileApp() {
  const [shell, setShell] = useState<ShellState>(readShellState);
  const [session, setSession] = useState(0);
  const wasPaired = useRef(shell.paired);
  useEffect(() => onShellState(setShell), []);
  useEffect(() => {
    if (shell.paired && !wasPaired.current) setSession((value) => value + 1);
    wasPaired.current = shell.paired;
  }, [shell.paired]);
  useEffect(() => { document.documentElement.dataset.loomTheme = "dark"; }, []);
  if (!shell.paired) return <Pair />;
  return <Session key={`${session}:${shell.deviceId}`} shell={shell} />;
}
