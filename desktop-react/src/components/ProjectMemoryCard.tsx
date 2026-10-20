import {
  Archive,
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  RotateCcw,
  Search,
  Sparkles,
  Trash2,
  X,
} from "./icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { withProjectReadTimeout } from "../state/projectRead";
import { useI18n } from "../i18n";

function readProject<T>(method: string, params: Record<string, unknown>): Promise<T> {
  if (!window.loom?.call) return Promise.reject(new Error("本地主机连接不可用，请重新连接后重试。"));
  return withProjectReadTimeout(window.loom.call<T>(method, params));
}

type ProjectMemoryRecord = {
  memory_id: string;
  scope: string;
  category: string;
  text: string;
  importance: number;
  source_count: number;
  usage_count: number;
  last_used_at: string;
  confidence: number;
  status: string;
  last_verified_at: string;
  archived_at: string;
  lifecycle_note: string;
  created_at: string;
  updated_at: string;
  score?: number;
  reasons?: string[];
};

type ProjectMemoryEvidence = {
  evidence_id: string;
  memory_id: string;
  source_session_id: string;
  source_turn_id: string;
  excerpt: string;
  created_at: string;
};

type ProjectMemoryUsage = {
  event_id: string;
  memory_id: string;
  source_session_id: string;
  source_turn_id: string;
  route: string;
  score: number;
  reason: string;
  created_at: string;
};

type ProjectMemoryStatus = {
  projectId: string;
  scope: string;
  total: number;
  visible: number;
  archived: number;
  categories: Record<string, number>;
  usage_events: number;
  skill_candidates: number;
  enabled: boolean;
  auto_extract: boolean;
  semantic_auto: boolean;
};

type ProjectMemoryIndex = {
  version: number;
  active: number;
  archived: number;
  categories: Record<string, number>;
  summary: string;
};

type ProjectMemoryReadResult = {
  memory: ProjectMemoryRecord;
  evidence: ProjectMemoryEvidence[];
  usage: ProjectMemoryUsage[];
};

type ProjectMemoryCardProps = {
  projectId: string;
  open: boolean;
  running: boolean;
};

function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : String(cause);
}

const CATEGORY_LABELS: Record<string, [zh: string, en: string]> = {
  decision: ["决策", "Decision"],
  project: ["项目", "Project"],
  fact: ["事实", "Fact"],
  constraint: ["约束", "Constraint"],
  preference: ["偏好", "Preference"],
};

function categoryLabel(value: string, zh: boolean): string {
  const label = CATEGORY_LABELS[value];
  return label ? label[zh ? 0 : 1] : value || (zh ? "记忆" : "Memory");
}

const ROUTE_LABELS: Record<string, [zh: string, en: string]> = {
  auto_route: ["自动召回", "Recalled"],
  index_fallback: ["索引回退", "Index fallback"],
  search: ["主动搜索", "Searched"],
  read: ["读取来源", "Read"],
  legacy: ["历史使用", "Earlier use"],
};

function routeLabel(value: string, zh: boolean): string {
  const label = ROUTE_LABELS[value];
  return label ? label[zh ? 0 : 1] : value || (zh ? "记忆" : "Memory");
}

function relativeTime(value: string | undefined, zh: boolean): string {
  const stamp = Date.parse(String(value || ""));
  if (!Number.isFinite(stamp)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - stamp) / 1000));
  if (seconds < 60) return zh ? "刚刚" : "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return zh ? `${minutes} 分钟前` : `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return zh ? `${hours} 小时前` : `${hours} h ago`;
  const days = Math.floor(hours / 24);
  return zh ? `${days} 天前` : `${days} d ago`;
}

function sourceLabel(count: number, zh: boolean): string {
  const value = Math.max(1, Number(count || 1));
  if (zh) return `${value} 个来源`;
  return value > 1 ? `${value} sources` : "1 source";
}

function compactIndexSummary(value: string): string[] {
  return String(value || "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("LOOM_MEMORY_INDEX") && !line.startsWith("active="));
}

export function ProjectMemoryCard({ projectId, open, running }: ProjectMemoryCardProps) {
  const zh = useI18n().language === "zh-CN";
  const [status, setStatus] = useState<ProjectMemoryStatus | null>(null);
  const [index, setIndex] = useState<ProjectMemoryIndex | null>(null);
  const [memories, setMemories] = useState<ProjectMemoryRecord[]>([]);
  const [view, setView] = useState<"active" | "archived">("active");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<ProjectMemoryReadResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [reading, setReading] = useState("");
  const [mutating, setMutating] = useState("");
  const [forgetTarget, setForgetTarget] = useState("");
  const [error, setError] = useState("");
  const listRequest = useRef(0);

  const loadStatus = useCallback(async () => {
    const result = await readProject<{ memory?: ProjectMemoryStatus }>(
      "project/memory_status",
      { projectId },
    );
    setStatus(result.memory ?? null);
  }, [projectId]);

  const loadIndex = useCallback(async () => {
    const result = await readProject<{ index?: ProjectMemoryIndex }>(
      "project/memory_index",
      { projectId, maxChars: 3600 },
    );
    setIndex(result.index ?? null);
  }, [projectId]);

  const loadList = useCallback(async (search: string, targetView: "active" | "archived") => {
    const request = ++listRequest.current;
    const needle = search.trim();
    if (targetView === "active" && needle) {
      const result = await readProject<{ memories?: ProjectMemoryRecord[] }>(
        "project/memory_search",
        { projectId, query: needle, limit: 32 },
      );
      if (request === listRequest.current) setMemories(Array.isArray(result.memories) ? result.memories : []);
      return;
    }
    const result = await readProject<{ memories?: ProjectMemoryRecord[] }>(
      "project/memory_list",
      { projectId, limit: 200, status: targetView },
    );
    if (request !== listRequest.current) return;
    const rows = Array.isArray(result.memories) ? result.memories : [];
    setMemories(
      targetView === "archived" && needle
        ? rows.filter((memory) => memory.text.toLocaleLowerCase().includes(needle.toLocaleLowerCase()))
        : rows,
    );
  }, [projectId]);

  const refresh = useCallback(async () => {
    if (!open || !projectId) return;
    setLoading(true);
    setError("");
    try {
      await Promise.all([loadStatus(), loadIndex(), loadList(query, view)]);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setLoading(false);
    }
  }, [loadIndex, loadList, loadStatus, open, projectId, query, view]);

  useEffect(() => {
    setQuery("");
    setView("active");
    setSelected(null);
    setForgetTarget("");
    if (!open || !projectId) {
      setStatus(null);
      setIndex(null);
      setMemories([]);
      return;
    }
    setLoading(true);
    setError("");
    void Promise.all([loadStatus(), loadIndex(), loadList("", "active")])
      .catch((cause) => setError(errorText(cause)))
      .finally(() => setLoading(false));
  }, [loadIndex, loadList, loadStatus, open, projectId]);

  useEffect(() => {
    if (!open || !projectId) return;
    const timer = window.setTimeout(() => {
      void loadList(query, view).catch((cause) => setError(errorText(cause)));
    }, 220);
    return () => window.clearTimeout(timer);
  }, [loadList, open, projectId, query, view]);

  const switchView = (next: "active" | "archived") => {
    setView(next);
    setSelected(null);
    setForgetTarget("");
  };

  const readMemory = async (memoryId: string) => {
    if (reading) return;
    if (selected?.memory.memory_id === memoryId) {
      setSelected(null);
      setForgetTarget("");
      return;
    }
    setReading(memoryId);
    setError("");
    try {
      const result = await readProject<ProjectMemoryReadResult>(
        "project/memory_read",
        { projectId, memoryId, evidenceLimit: 24, usageLimit: 20 },
      );
      setSelected(result);
      setForgetTarget("");
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setReading("");
    }
  };

  const changeLifecycle = async (memoryId: string, action: "archive" | "restore") => {
    if (running || mutating) return;
    setMutating(memoryId);
    setError("");
    try {
      const method = action === "archive" ? "project/memory_archive" : "project/memory_restore";
      const result = await window.loom.call<{ archived?: boolean; restored?: boolean }>(
        method,
        { projectId, memoryId },
      );
      const ok = action === "archive" ? result.archived : result.restored;
      if (!ok) throw new Error(action === "archive"
        ? (zh ? "这条记忆无法归档。" : "This memory could not be archived.")
        : (zh ? "这条记忆无法恢复。" : "This memory could not be restored."));
      setSelected(null);
      setForgetTarget("");
      await Promise.all([loadStatus(), loadIndex(), loadList(query, view)]);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setMutating("");
    }
  };

  const forgetMemory = async (memoryId: string) => {
    if (running || mutating) return;
    if (forgetTarget !== memoryId) {
      setForgetTarget(memoryId);
      return;
    }
    setMutating(memoryId);
    setError("");
    try {
      const result = await window.loom.call<{ forgotten?: boolean }>(
        "project/memory_forget",
        { projectId, memoryId },
      );
      if (!result.forgotten) throw new Error(zh ? "这条项目记忆已经不存在或不属于当前项目。" : "This memory no longer exists or belongs to another project.");
      setSelected(null);
      setForgetTarget("");
      await Promise.all([loadStatus(), loadIndex(), loadList(query, view)]);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setMutating("");
    }
  };

  const categories = status?.categories ?? {};
  const chips = useMemo(
    () => ["decision", "project", "fact", "constraint", "preference"]
      .map((category) => ({ category, count: Number(categories[category] || 0) }))
      .filter((item) => item.count > 0),
    [categories],
  );
  const indexLines = useMemo(() => compactIndexSummary(index?.summary || ""), [index?.summary]);

  return (
    <section className="project-memory-card">
      <div className="project-card-heading">
        <div>
          <strong>{zh ? "项目记忆" : "Project memory"}</strong>
        </div>
        <button type="button" onClick={() => void refresh()} disabled={loading} title={zh ? "刷新项目记忆" : "Refresh project memory"}>
          <RefreshCw size={14} strokeWidth={1.85} spinning={loading} />
          {loading ? (zh ? "刷新中" : "Refreshing") : (zh ? "刷新" : "Refresh")}
        </button>
      </div>

      <p className="project-memory-help">
        {zh
          ? "分层索引先找到相关的记忆，再按需读取证据；过期的低价值事实会进入可恢复的归档，而不会直接删除。"
          : "A layered index finds the relevant memories first and reads their evidence when needed; stale, low-value facts move to a restorable archive instead of being deleted."}
      </p>

      <div className="project-memory-overview">
        <div className="project-memory-total">
          <BrainCircuit size={16} strokeWidth={1.8} />
          <span>{zh ? "当前记忆" : "Memories"}</span>
          <strong>{status?.total ?? 0}</strong>
          <small>
            {status?.enabled === false
              ? (zh ? "记忆已关闭，现有记忆仍可查看" : "Memory is off; existing memories can still be read")
              : (zh ? `已使用 ${status?.usage_events ?? 0} 次` : `used ${status?.usage_events ?? 0} times`)}
          </small>
        </div>
        <div className="project-memory-category-list" aria-label={zh ? "项目记忆分类" : "Project memory categories"}>
          {chips.length ? chips.map((item) => (
            <span className={`project-memory-category ${item.category}`} key={item.category}>
              {categoryLabel(item.category, zh)}
              <b>{item.count}</b>
            </span>
          )) : <span className="project-memory-category empty">{zh ? "暂无分类" : "No categories"}</span>}
          {Number(status?.skill_candidates || 0) > 0 ? (
            <span className="project-memory-category skill">
              <Sparkles size={10} />
              {zh ? "Skill 候选" : "Skill candidates"}
              <b>{status?.skill_candidates}</b>
            </span>
          ) : null}
        </div>
      </div>

      {indexLines.length ? (
        <div className="project-memory-index">
          <div>
            <strong>{zh ? "知识索引" : "Knowledge index"}</strong>
          </div>
          <ul>
            {indexLines.slice(0, 5).map((line) => <li key={line}>{line.replace(/^[-]\s*/, "")}</li>)}
          </ul>
        </div>
      ) : null}

      {running ? (
        <div className="project-memory-note">
          {zh
            ? "项目里有任务正在运行。记忆仍可查看，等任务结束后才能归档、恢复或删除。"
            : "A task is running in this project. Memories can be read, and archived, restored or deleted once it ends."}
        </div>
      ) : null}

      {error ? <div className="project-memory-error">{error}</div> : null}

      <div className="project-memory-toolbar">
        <div className="project-memory-tabs">
          <button type="button" className={view === "active" ? "active" : ""} onClick={() => switchView("active")}>
            {zh ? "当前" : "Active"} <b>{status?.total ?? 0}</b>
          </button>
          <button type="button" className={view === "archived" ? "active" : ""} onClick={() => switchView("archived")}>
            {zh ? "已归档" : "Archived"} <b>{status?.archived ?? 0}</b>
          </button>
        </div>
        <label className="project-memory-search">
          <Search size={14} strokeWidth={1.9} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={view === "active" ? (zh ? "搜索决策、事实、约束…" : "Search decisions, facts, constraints…") : (zh ? "筛选已归档记忆…" : "Filter archived memories…")}
            aria-label={zh ? "搜索项目记忆" : "Search project memory"}
          />
          {query ? (
            <button type="button" onClick={() => setQuery("")} aria-label={zh ? "清除搜索" : "Clear project memory search"}>
              <X size={13} strokeWidth={1.9} />
            </button>
          ) : null}
        </label>
      </div>

      <div className="project-memory-list" aria-busy={loading}>
        {memories.length ? memories.map((memory) => {
          const active = selected?.memory.memory_id === memory.memory_id;
          const busy = reading === memory.memory_id;
          return (
            <article className={`project-memory-row ${active ? "selected" : ""}`} key={memory.memory_id}>
              <button type="button" className="project-memory-row-main" onClick={() => void readMemory(memory.memory_id)}>
                <span className={`project-memory-type ${memory.category}`}>{categoryLabel(memory.category, zh)}</span>
                <span className="project-memory-row-copy">
                  <strong>{memory.text}</strong>
                  <small>
                    {sourceLabel(memory.source_count, zh)}
                    <span>·</span>
                    {memory.usage_count ? (zh ? `使用 ${memory.usage_count} 次` : `used ${memory.usage_count} times`) : (zh ? "尚未使用" : "not used yet")}
                    <span>·</span>
                    {relativeTime(memory.status === "archived" ? memory.archived_at : memory.updated_at, zh)}
                  </small>
                </span>
                {busy ? <RefreshCw size={13} spinning /> : active ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>

              {active && selected ? (
                <div className="project-memory-detail">
                  <div className="project-memory-detail-meta">
                    <span>{zh ? `重要度 ${selected.memory.importance}/5` : `importance ${selected.memory.importance}/5`}</span>
                    <span>{sourceLabel(selected.memory.source_count, zh)}</span>
                    <span>{zh ? `${selected.evidence.length} 条证据` : `${selected.evidence.length} evidence`}</span>
                    <span>{zh ? `${selected.memory.usage_count} 次使用` : `used ${selected.memory.usage_count} times`}</span>
                    {selected.memory.score ? <span>{zh ? `检索分 ${selected.memory.score.toFixed(2)}` : `score ${selected.memory.score.toFixed(2)}`}</span> : null}
                  </div>

                  {selected.memory.lifecycle_note ? (
                    <p className="project-memory-lifecycle-note">{selected.memory.lifecycle_note}</p>
                  ) : null}

                  {selected.evidence.length ? (
                    <div className="project-memory-evidence">
                      {selected.evidence.slice(0, 6).map((evidence) => (
                        <div key={evidence.evidence_id}>
                          <span>{evidence.excerpt}</span>
                          <small>
                            {zh ? "来源对话" : "from conversation"} {evidence.source_session_id.slice(0, 8)}
                            {evidence.created_at ? ` · ${relativeTime(evidence.created_at, zh)}` : ""}
                          </small>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="project-memory-no-evidence">{zh ? "这条记忆没有可显示的来源摘录。" : "No source excerpt to show for this memory."}</p>
                  )}

                  {selected.usage?.length ? (
                    <div className="project-memory-usage">
                      <strong>{zh ? "最近使用" : "Recent use"}</strong>
                      {selected.usage.slice(0, 4).map((event) => (
                        <div key={event.event_id}>
                          <span>{routeLabel(event.route, zh)}</span>
                          <small>
                            {event.score > 0 ? `score ${event.score.toFixed(2)} · ` : ""}
                            {relativeTime(event.created_at, zh)}
                          </small>
                        </div>
                      ))}
                    </div>
                  ) : null}

                  <div className="project-memory-detail-actions">
                    <span>
                      {forgetTarget === memory.memory_id
                        ? (zh ? "再次点击确认永久删除。原对话不会被删除。" : "Click again to delete it for good. The conversations stay.")
                        : memory.status === "archived"
                          ? (zh ? "归档的记忆不会自动进入模型上下文，可随时恢复。" : "An archived memory is not recalled into the model's context; restore it any time.")
                          : (zh ? "归档会停止自动召回，但保留来源和历史。" : "Archiving stops recall and keeps its sources and history.")}
                    </span>
                    {memory.status === "archived" ? (
                      <button
                        type="button"
                        className="lifecycle"
                        disabled={running || Boolean(mutating)}
                        onClick={() => void changeLifecycle(memory.memory_id, "restore")}
                      >
                        <RotateCcw size={13} strokeWidth={1.85} />
                        {mutating === memory.memory_id ? (zh ? "处理中" : "Working") : (zh ? "恢复" : "Restore")}
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="lifecycle"
                        disabled={running || Boolean(mutating)}
                        onClick={() => void changeLifecycle(memory.memory_id, "archive")}
                      >
                        <Archive size={13} strokeWidth={1.85} />
                        {mutating === memory.memory_id ? (zh ? "处理中" : "Working") : (zh ? "归档" : "Archive")}
                      </button>
                    )}
                    <button
                      type="button"
                      className={forgetTarget === memory.memory_id ? "confirm" : ""}
                      disabled={running || Boolean(mutating)}
                      onClick={() => void forgetMemory(memory.memory_id)}
                    >
                      <Trash2 size={13} strokeWidth={1.85} />
                      {mutating === memory.memory_id ? (zh ? "处理中" : "Working") : forgetTarget === memory.memory_id ? (zh ? "确认删除" : "Confirm delete") : (zh ? "删除" : "Delete")}
                    </button>
                  </div>
                </div>
              ) : null}
            </article>
          );
        }) : (
          <div className="project-memory-empty">
            {view === "archived" ? <Archive size={19} strokeWidth={1.7} /> : <BrainCircuit size={19} strokeWidth={1.7} />}
            <strong>
              {query.trim()
                ? (zh ? "没有匹配的项目记忆" : "No matching memories")
                : view === "archived"
                  ? (zh ? "还没有归档的记忆" : "Nothing archived yet")
                  : (zh ? "这个项目还没有长期记忆" : "No long-term memory in this project yet")}
            </strong>
            <span>
              {query.trim()
                ? (zh ? "换个关键词试试。" : "Try other words.")
                : view === "archived"
                  ? (zh ? "低价值且长期未使用的事实会安全地进入这里，也可以手动归档。" : "Low-value facts that go unused for long move here safely; you can archive by hand too.")
                  : (zh ? "完成一些项目对话后，Loom 会在后台提取并整理值得长期保留的上下文。" : "After a few conversations in this project, Loom extracts and organises the context worth keeping, in the background.")}
            </span>
          </div>
        )}
      </div>
    </section>
  );
}
