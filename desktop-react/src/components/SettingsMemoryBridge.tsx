import {
  BrainCircuit,
  ChevronRight,
  CircleAlert,
  Clock3,
  Database,
  Eye,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import "./settings-memory.css";

type MemoryPreferences = {
  enabled: boolean;
  autoExtract: boolean;
  semanticAuto: boolean;
  idleSeconds: number;
};

type MemoryRecordView = {
  memory_id: string;
  scope: "global" | "workspace" | string;
  category: string;
  text: string;
  importance: number;
  source_count: number;
  usage_count: number;
  last_used_at: string;
  confidence: number;
  status: string;
  last_verified_at: string;
  created_at: string;
  updated_at: string;
};

type MemoryEvidenceView = {
  evidence_id: string;
  memory_id: string;
  source_session_id: string;
  source_turn_id: string;
  excerpt: string;
  created_at: string;
};

type MemoryStatus = {
  enabled?: boolean;
  visible?: number;
  total?: number;
  pending?: number;
  evidence?: number;
  pending_jobs?: number;
  failure_count?: number;
  semantic_pending?: number;
  semantic_running?: number;
  semantic_retry?: number;
  semantic_completed?: number;
  configured?: Partial<MemoryPreferences>;
  last_success_at?: string;
  last_error?: string;
};

type MemoryReadResult = {
  memory: MemoryRecordView;
  evidence: MemoryEvidenceView[];
};

type MemorySettingsBridgeProps = {
  threadId?: string | null;
  running: boolean;
};

const DEFAULT_MEMORY: MemoryPreferences = {
  enabled: true,
  autoExtract: true,
  semanticAuto: true,
  idleSeconds: 45,
};

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

function compactDate(value?: string): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function categoryLabel(value: string): string {
  const labels: Record<string, string> = {
    preference: "Preference",
    fact: "Fact",
    project: "Project",
    decision: "Decision",
    constraint: "Constraint",
  };
  return labels[value] || value || "Memory";
}

function scopeLabel(value: string): string {
  if (value === "global") return "Global";
  if (value === "workspace") return "Workspace";
  return value || "Memory";
}

function MemorySwitch({
  checked,
  disabled,
  label,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  label: string;
  onChange(value: boolean): void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className={`settings-switch ${checked ? "on" : ""}`}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span />
    </button>
  );
}

function MemoryPill({ tone = "muted", children }: { tone?: string; children: string }) {
  return (
    <span className={`settings-status-pill ${tone}`}>
      <span className="settings-status-dot" />
      {children}
    </span>
  );
}

function MemoryPanel({ threadId, running }: MemorySettingsBridgeProps) {
  const [preferences, setPreferences] = useState<MemoryPreferences>(DEFAULT_MEMORY);
  const [status, setStatus] = useState<MemoryStatus | null>(null);
  const [memories, setMemories] = useState<MemoryRecordView[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<MemoryReadResult | null>(null);
  const [reading, setReading] = useState("");
  const [forgetTarget, setForgetTarget] = useState("");
  const [forgetting, setForgetting] = useState("");

  const disabledByTurn = running;
  const listTitle = query.trim() ? "Search results" : "Visible memories";

  const loadSettings = async () => {
    const result = await window.loom.call<{ settings?: { memory?: Partial<MemoryPreferences> } }>("settings/get", {});
    setPreferences({ ...DEFAULT_MEMORY, ...(result.settings?.memory ?? {}) });
  };

  const loadStatus = async () => {
    if (!threadId) {
      setStatus(null);
      return;
    }
    const result = await window.loom.call<{ memory?: MemoryStatus }>("memory/status", { threadId });
    setStatus(result.memory ?? null);
  };

  const loadList = async (search = query) => {
    if (!threadId) {
      setMemories([]);
      return;
    }
    const needle = search.trim();
    if (needle) {
      const result = await window.loom.call<{ memories?: MemoryRecordView[] }>("memory/search", {
        threadId,
        query: needle,
        limit: 32,
      });
      setMemories(Array.isArray(result.memories) ? result.memories : []);
      return;
    }
    const result = await window.loom.call<{ memories?: MemoryRecordView[] }>("memory/list", {
      threadId,
      limit: 200,
    });
    setMemories(Array.isArray(result.memories) ? result.memories : []);
  };

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      await loadSettings();
      await Promise.all([loadStatus(), loadList()]);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  useEffect(() => {
    if (!threadId) return;
    const timer = window.setTimeout(() => {
      void loadList(query).catch((cause) => setError(errorText(cause)));
    }, 260);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, threadId]);

  const savePreference = async <K extends keyof MemoryPreferences>(
    key: K,
    value: MemoryPreferences[K],
  ) => {
    if (disabledByTurn || saving) return;
    const previous = preferences;
    const next = { ...preferences, [key]: value };
    setPreferences(next);
    setSaving(String(key));
    setError("");
    try {
      const result = await window.loom.call<{ settings?: { memory?: Partial<MemoryPreferences> } }>("settings/set", {
        path: `memory.${String(key)}`,
        value,
      });
      setPreferences({ ...DEFAULT_MEMORY, ...(result.settings?.memory ?? next) });
      if (threadId) await loadStatus();
    } catch (cause) {
      setPreferences(previous);
      setError(errorText(cause));
    } finally {
      setSaving("");
    }
  };

  const readMemory = async (memoryId: string) => {
    if (!threadId || reading) return;
    setReading(memoryId);
    setError("");
    try {
      const result = await window.loom.call<MemoryReadResult>("memory/read", {
        threadId,
        memoryId,
        evidenceLimit: 30,
      });
      setSelected(result);
      setForgetTarget("");
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setReading("");
    }
  };

  const forgetMemory = async (memoryId: string) => {
    if (!threadId || forgetting) return;
    if (forgetTarget !== memoryId) {
      setForgetTarget(memoryId);
      return;
    }
    setForgetting(memoryId);
    setError("");
    try {
      const result = await window.loom.call<{ forgotten?: boolean }>("memory/forget", {
        threadId,
        memoryId,
      });
      if (!result.forgotten) throw new Error("Loom did not remove this memory.");
      if (selected?.memory.memory_id === memoryId) setSelected(null);
      setForgetTarget("");
      await Promise.all([loadList(), loadStatus()]);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setForgetting("");
    }
  };

  const semanticQueue = Number(status?.semantic_pending || 0)
    + Number(status?.semantic_running || 0)
    + Number(status?.semantic_retry || 0);

  const stats = useMemo(() => [
    { label: "Visible", value: status?.visible ?? (threadId ? memories.length : "—"), detail: "current workspace + global" },
    { label: "Stored", value: status?.total ?? "—", detail: "active canonical memories" },
    { label: "Evidence", value: status?.evidence ?? "—", detail: "source excerpts retained" },
    { label: "Stage 1", value: status?.pending_jobs ?? 0, detail: "background extraction jobs" },
    { label: "Stage 2", value: semanticQueue, detail: "semantic reconciliation queue" },
  ], [memories.length, semanticQueue, status, threadId]);

  return (
    <div className="memory-settings-page">
      <div className="settings-page-heading settings-heading-with-switch memory-page-heading">
        <div>
          <span className="settings-eyebrow">Long-term context</span>
          <h1>Memory</h1>
          <p>Loom keeps durable preferences, facts, project decisions, and constraints locally. Current instructions and observed runtime state always take precedence over stored memory.</p>
        </div>
        <div className="settings-master-switch">
          <MemoryPill tone={preferences.enabled ? "ready" : "off"}>{preferences.enabled ? "Active" : "Off"}</MemoryPill>
          <MemorySwitch
            checked={preferences.enabled}
            disabled={disabledByTurn || Boolean(saving)}
            label="Toggle long-term memory"
            onChange={(value) => void savePreference("enabled", value)}
          />
        </div>
      </div>

      {running ? (
        <div className="settings-callout warning memory-callout">
          <CircleAlert size={16} />
          <div><strong>Finish or stop the active turn first.</strong><span>Memory settings are not changed mid-execution.</span></div>
        </div>
      ) : null}

      {!preferences.enabled ? (
        <div className="settings-callout memory-callout">
          <ShieldCheck size={16} />
          <div><strong>Memory use is disabled.</strong><span>Extraction, semantic consolidation, summary injection, and model-side memory tools are off. Stored memories remain available below for review or deletion.</span></div>
        </div>
      ) : null}

      {error ? (
        <div className="settings-callout warning memory-callout">
          <CircleAlert size={16} />
          <div><strong>Memory request failed.</strong><span>{error}</span></div>
        </div>
      ) : null}

      <section className="settings-section">
        <div className="settings-section-heading"><h2>Runtime overview</h2><p>Live state for the current conversation's visible memory scope.</p></div>
        <div className="memory-stat-grid">
          {stats.map((item) => (
            <div className="memory-stat-card" key={item.label}>
              <span>{item.label}</span>
              <strong>{String(item.value)}</strong>
              <small>{item.detail}</small>
            </div>
          ))}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-heading"><h2>Memory behavior</h2><p>These preferences are persisted by the App Server and take effect without restarting Loom.</p></div>
        <div className="settings-card memory-preference-list">
          <div className="memory-preference-row">
            <span className="memory-preference-icon"><Database size={17} /></span>
            <div><strong>Automatic extraction</strong><span>After a completed turn, extract only durable information from observable conversation events.</span></div>
            <MemorySwitch checked={preferences.autoExtract} disabled={!preferences.enabled || disabledByTurn || Boolean(saving)} label="Automatic memory extraction" onChange={(value) => void savePreference("autoExtract", value)} />
          </div>
          <div className="memory-preference-row">
            <span className="memory-preference-icon"><BrainCircuit size={17} /></span>
            <div><strong>Semantic consolidation</strong><span>Reconcile new memories with related older memories using conservative keep, update, merge, or supersede operations.</span></div>
            <MemorySwitch checked={preferences.semanticAuto} disabled={!preferences.enabled || disabledByTurn || Boolean(saving)} label="Semantic memory consolidation" onChange={(value) => void savePreference("semanticAuto", value)} />
          </div>
          <div className="memory-preference-row">
            <span className="memory-preference-icon"><Clock3 size={17} /></span>
            <div><strong>Idle window</strong><span>Wait briefly after the last completed turn so rapid corrections can be extracted together.</span></div>
            <select
              className="mature-select"
              value={String(preferences.idleSeconds)}
              disabled={!preferences.enabled || !preferences.autoExtract || disabledByTurn || Boolean(saving)}
              aria-label="Memory idle window"
              onChange={(event) => void savePreference("idleSeconds", Number(event.target.value))}
            >
              {[0, 15, 30, 45, 60, 120, 300].map((seconds) => <option value={seconds} key={seconds}>{seconds === 0 ? "Immediate" : `${seconds}s`}</option>)}
            </select>
          </div>
        </div>
      </section>

      <section className="settings-section">
        <div className="memory-section-heading">
          <div className="settings-section-heading"><h2>{listTitle}</h2><p>Only active memories visible to the current conversation are shown. Superseded history stays in the durable audit trail.</p></div>
          <button type="button" className="memory-refresh-button" disabled={loading} onClick={() => void refresh()}><RefreshCw size={14} className={loading ? "spin" : ""} />Refresh</button>
        </div>

        {!threadId ? (
          <div className="memory-empty-state">
            <BrainCircuit size={22} />
            <strong>Open a conversation to inspect memory.</strong>
            <span>The settings above remain available without a conversation, but workspace-visible memory is resolved from the active thread.</span>
          </div>
        ) : (
          <div className="memory-browser">
            <label className="memory-search-box">
              <Search size={15} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search memories, decisions, preferences…" />
              {query ? <button type="button" aria-label="Clear memory search" onClick={() => setQuery("")}><X size={14} /></button> : null}
            </label>

            <div className="memory-browser-body">
              <div className="memory-list" aria-busy={loading}>
                {memories.map((memory) => (
                  <article className={`memory-row ${selected?.memory.memory_id === memory.memory_id ? "selected" : ""}`} key={memory.memory_id}>
                    <button type="button" className="memory-row-main" onClick={() => void readMemory(memory.memory_id)}>
                      <div className="memory-row-badges">
                        <span>{scopeLabel(memory.scope)}</span>
                        <span>{categoryLabel(memory.category)}</span>
                        <span>Importance {memory.importance}</span>
                      </div>
                      <strong>{memory.text}</strong>
                      <small>Updated {compactDate(memory.updated_at)} · {memory.source_count || 1} source{memory.source_count === 1 ? "" : "s"} · used {memory.usage_count || 0}×</small>
                    </button>
                    <button type="button" className="memory-row-open" aria-label="Read memory" onClick={() => void readMemory(memory.memory_id)} disabled={reading === memory.memory_id}><ChevronRight size={15} /></button>
                  </article>
                ))}
                {!loading && memories.length === 0 ? (
                  <div className="memory-list-empty"><Database size={19} /><strong>{query.trim() ? "No matching memories" : "No memories yet"}</strong><span>{query.trim() ? "Try a broader search." : "Durable memory will appear here after successful extraction."}</span></div>
                ) : null}
              </div>

              <aside className={`memory-detail ${selected ? "open" : ""}`}>
                {selected ? (
                  <>
                    <div className="memory-detail-head">
                      <div><span>{scopeLabel(selected.memory.scope)} · {categoryLabel(selected.memory.category)}</span><strong>Memory detail</strong></div>
                      <button type="button" aria-label="Close memory detail" onClick={() => { setSelected(null); setForgetTarget(""); }}><X size={15} /></button>
                    </div>
                    <div className="memory-detail-copy">{selected.memory.text}</div>
                    <div className="memory-detail-meta">
                      <div><span>Importance</span><strong>{selected.memory.importance}/5</strong></div>
                      <div><span>Sources</span><strong>{selected.memory.source_count}</strong></div>
                      <div><span>Usage</span><strong>{selected.memory.usage_count}×</strong></div>
                      <div><span>Updated</span><strong>{compactDate(selected.memory.updated_at)}</strong></div>
                    </div>
                    <div className="memory-evidence-heading"><Eye size={14} /><strong>Evidence</strong><span>{selected.evidence.length}</span></div>
                    <div className="memory-evidence-list">
                      {selected.evidence.map((item) => (
                        <div className="memory-evidence-item" key={item.evidence_id}>
                          <p>{item.excerpt || "No excerpt stored."}</p>
                          <small>{compactDate(item.created_at)} · turn {item.source_turn_id || "unknown"}</small>
                        </div>
                      ))}
                      {selected.evidence.length === 0 ? <div className="memory-evidence-empty">No evidence records are attached to this memory.</div> : null}
                    </div>
                    <div className="memory-detail-actions">
                      <button
                        type="button"
                        className={forgetTarget === selected.memory.memory_id ? "confirm" : ""}
                        disabled={forgetting === selected.memory.memory_id}
                        onClick={() => void forgetMemory(selected.memory.memory_id)}
                      >
                        <Trash2 size={14} />
                        {forgetting === selected.memory.memory_id ? "Forgetting…" : forgetTarget === selected.memory.memory_id ? "Confirm forget" : "Forget memory"}
                      </button>
                      {forgetTarget === selected.memory.memory_id ? <span>This removes the canonical memory and its candidate/evidence copies.</span> : null}
                    </div>
                  </>
                ) : (
                  <div className="memory-detail-placeholder"><Eye size={21} /><strong>Select a memory</strong><span>Read the full canonical text and provenance before deciding whether to keep or forget it.</span></div>
                )}
              </aside>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

export function SettingsMemoryBridge({ threadId, running }: MemorySettingsBridgeProps) {
  const [open, setOpen] = useState(false);
  const [navHost] = useState(() => document.createElement("span"));
  const [contentHost] = useState(() => document.createElement("div"));

  useEffect(() => {
    navHost.className = "settings-memory-nav-host";
    contentHost.className = "settings-memory-content-host";

    const syncHosts = () => {
      const shell = document.querySelector<HTMLElement>(".settings-shell");
      const nav = document.querySelector<HTMLElement>(".settings-nav");
      const mainScroll = document.querySelector<HTMLElement>(".settings-main-scroll");
      if (!shell || !nav || !mainScroll) return;

      const loomSection = Array.from(nav.querySelectorAll<HTMLElement>(":scope > section")).find((section) =>
        section.querySelector(".settings-nav-label")?.textContent?.trim() === "Loom",
      );
      if (loomSection) {
        const permissions = Array.from(loomSection.querySelectorAll<HTMLButtonElement>(":scope > button")).find((button) =>
          button.textContent?.trim() === "Permissions",
        );
        if (navHost.parentElement !== loomSection) {
          if (permissions) loomSection.insertBefore(navHost, permissions);
          else loomSection.appendChild(navHost);
        } else if (permissions && navHost.nextSibling !== permissions) {
          loomSection.insertBefore(navHost, permissions);
        }
      } else {
        navHost.remove();
        if (open) setOpen(false);
      }

      if (contentHost.parentElement !== mainScroll) mainScroll.appendChild(contentHost);
      shell.classList.toggle("settings-memory-mode", open);
    };

    const closeForNativeNavigation = (event: Event) => {
      const target = event.target instanceof Element ? event.target.closest("button") : null;
      if (!target || target.closest(".settings-memory-nav-host")) return;
      if (target.closest(".settings-nav")) setOpen(false);
    };

    syncHosts();
    const observer = new MutationObserver(syncHosts);
    observer.observe(document.body, { childList: true, subtree: true });
    document.addEventListener("click", closeForNativeNavigation, true);

    return () => {
      observer.disconnect();
      document.removeEventListener("click", closeForNativeNavigation, true);
      document.querySelector(".settings-shell")?.classList.remove("settings-memory-mode");
      navHost.remove();
      contentHost.remove();
    };
  }, [contentHost, navHost, open]);

  return (
    <>
      {createPortal(
        <button type="button" className={open ? "active" : ""} onClick={() => setOpen(true)}>
          <BrainCircuit size={16} strokeWidth={1.7} />
          <span>Memory</span>
        </button>,
        navHost,
      )}
      {createPortal(open ? <MemoryPanel threadId={threadId} running={running} /> : null, contentHost)}
    </>
  );
}
