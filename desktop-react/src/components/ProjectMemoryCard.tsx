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
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

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

function categoryLabel(value: string): string {
  const labels: Record<string, string> = {
    decision: "决策",
    project: "项目",
    fact: "事实",
    constraint: "约束",
    preference: "偏好",
  };
  return labels[value] || value || "记忆";
}

function routeLabel(value: string): string {
  const labels: Record<string, string> = {
    auto_route: "自动召回",
    index_fallback: "索引回退",
    search: "主动搜索",
    read: "读取来源",
    legacy: "历史使用",
  };
  return labels[value] || value || "Memory";
}

function relativeTime(value?: string): string {
  const stamp = Date.parse(String(value || ""));
  if (!Number.isFinite(stamp)) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - stamp) / 1000));
  if (seconds < 60) return "刚刚";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  return `${days} 天前`;
}

function sourceLabel(count: number): string {
  const value = Math.max(1, Number(count || 1));
  return value > 1 ? `${value} 个来源` : "1 个来源";
}

function compactIndexSummary(value: string): string[] {
  return String(value || "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("LOOM_MEMORY_INDEX") && !line.startsWith("active="));
}

export function ProjectMemoryCard({ projectId, open, running }: ProjectMemoryCardProps) {
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

  const loadStatus = useCallback(async () => {
    const result = await window.loom.call<{ memory?: ProjectMemoryStatus }>(
      "project/memory_status",
      { projectId },
    );
    setStatus(result.memory ?? null);
  }, [projectId]);

  const loadIndex = useCallback(async () => {
    const result = await window.loom.call<{ index?: ProjectMemoryIndex }>(
      "project/memory_index",
      { projectId, maxChars: 3600 },
    );
    setIndex(result.index ?? null);
  }, [projectId]);

  const loadList = useCallback(async (search: string, targetView = view) => {
    const needle = search.trim();
    if (targetView === "active" && needle) {
      const result = await window.loom.call<{ memories?: ProjectMemoryRecord[] }>(
        "project/memory_search",
        { projectId, query: needle, limit: 32 },
      );
      setMemories(Array.isArray(result.memories) ? result.memories : []);
      return;
    }
    const result = await window.loom.call<{ memories?: ProjectMemoryRecord[] }>(
      "project/memory_list",
      { projectId, limit: 200, status: targetView },
    );
    const rows = Array.isArray(result.memories) ? result.memories : [];
    setMemories(
      targetView === "archived" && needle
        ? rows.filter((memory) => memory.text.toLocaleLowerCase().includes(needle.toLocaleLowerCase()))
        : rows,
    );
  }, [projectId, view]);

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
      const result = await window.loom.call<ProjectMemoryReadResult>(
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
      if (!ok) throw new Error(action === "archive" ? "这条记忆无法归档。" : "这条记忆无法恢复。");
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
      if (!result.forgotten) throw new Error("这条项目记忆已经不存在或不属于当前项目。");
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
          <span>Project Memory v3</span>
          <strong>项目记忆</strong>
        </div>
        <button type="button" onClick={() => void refresh()} disabled={loading} title="刷新项目记忆">
          <RefreshCw size={14} strokeWidth={1.85} className={loading ? "spin" : ""} />
          {loading ? "刷新中" : "刷新"}
        </button>
      </div>

      <p className="project-memory-help">
        分层索引会先路由到相关记忆，再按需读取证据；过期的低价值事实会进入可恢复归档，而不会直接删除。
      </p>

      <div className="project-memory-overview">
        <div className="project-memory-total">
          <BrainCircuit size={16} strokeWidth={1.8} />
          <span>当前记忆</span>
          <strong>{status?.total ?? 0}</strong>
          <small>
            {status?.enabled === false
              ? "Memory 已关闭，现有记忆仍可查看"
              : `v3 routing · ${status?.usage_events ?? 0} 次使用记录`}
          </small>
        </div>
        <div className="project-memory-category-list" aria-label="Project memory categories">
          {chips.length ? chips.map((item) => (
            <span className={`project-memory-category ${item.category}`} key={item.category}>
              {categoryLabel(item.category)}
              <b>{item.count}</b>
            </span>
          )) : <span className="project-memory-category empty">暂无分类</span>}
          {Number(status?.skill_candidates || 0) > 0 ? (
            <span className="project-memory-category skill">
              <Sparkles size={10} />
              Skill 候选
              <b>{status?.skill_candidates}</b>
            </span>
          ) : null}
        </div>
      </div>

      {indexLines.length ? (
        <div className="project-memory-index">
          <div>
            <span>Knowledge Index</span>
            <strong>项目知识索引</strong>
          </div>
          <ul>
            {indexLines.slice(0, 5).map((line) => <li key={line}>{line.replace(/^[-]\s*/, "")}</li>)}
          </ul>
        </div>
      ) : null}

      {running ? (
        <div className="project-memory-note">
          项目里有任务正在运行。记忆仍可查看，但需要等任务结束后才能归档、恢复或删除。
        </div>
      ) : null}

      {error ? <div className="project-memory-error">{error}</div> : null}

      <div className="project-memory-toolbar">
        <div className="project-memory-tabs">
          <button type="button" className={view === "active" ? "active" : ""} onClick={() => switchView("active")}>
            当前 <b>{status?.total ?? 0}</b>
          </button>
          <button type="button" className={view === "archived" ? "active" : ""} onClick={() => switchView("archived")}>
            已归档 <b>{status?.archived ?? 0}</b>
          </button>
        </div>
        <label className="project-memory-search">
          <Search size={14} strokeWidth={1.9} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={view === "active" ? "搜索决策、事实、约束…" : "筛选已归档记忆…"}
            aria-label="Search project memory"
          />
          {query ? (
            <button type="button" onClick={() => setQuery("")} aria-label="Clear project memory search">
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
                <span className={`project-memory-type ${memory.category}`}>{categoryLabel(memory.category)}</span>
                <span className="project-memory-row-copy">
                  <strong>{memory.text}</strong>
                  <small>
                    {sourceLabel(memory.source_count)}
                    <span>·</span>
                    {memory.usage_count ? `使用 ${memory.usage_count} 次` : "尚未使用"}
                    <span>·</span>
                    {relativeTime(memory.status === "archived" ? memory.archived_at : memory.updated_at)}
                  </small>
                </span>
                {busy ? <RefreshCw size={13} className="spin" /> : active ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              </button>

              {active && selected ? (
                <div className="project-memory-detail">
                  <div className="project-memory-detail-meta">
                    <span>重要度 {selected.memory.importance}/5</span>
                    <span>{sourceLabel(selected.memory.source_count)}</span>
                    <span>{selected.evidence.length} 条证据</span>
                    <span>{selected.memory.usage_count} 次使用</span>
                    {selected.memory.score ? <span>检索分 {selected.memory.score.toFixed(2)}</span> : null}
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
                            来源对话 {evidence.source_session_id.slice(0, 8)}
                            {evidence.created_at ? ` · ${relativeTime(evidence.created_at)}` : ""}
                          </small>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="project-memory-no-evidence">这条记忆没有可显示的来源摘录。</p>
                  )}

                  {selected.usage?.length ? (
                    <div className="project-memory-usage">
                      <strong>最近使用</strong>
                      {selected.usage.slice(0, 4).map((event) => (
                        <div key={event.event_id}>
                          <span>{routeLabel(event.route)}</span>
                          <small>
                            {event.score > 0 ? `score ${event.score.toFixed(2)} · ` : ""}
                            {relativeTime(event.created_at)}
                          </small>
                        </div>
                      ))}
                    </div>
                  ) : null}

                  <div className="project-memory-detail-actions">
                    <span>
                      {forgetTarget === memory.memory_id
                        ? "再次点击确认永久删除。原对话不会被删除。"
                        : memory.status === "archived"
                          ? "归档记忆不会自动进入模型上下文，可随时恢复。"
                          : "归档会停止自动召回，但保留来源和历史。"}
                    </span>
                    {memory.status === "archived" ? (
                      <button
                        type="button"
                        className="lifecycle"
                        disabled={running || Boolean(mutating)}
                        onClick={() => void changeLifecycle(memory.memory_id, "restore")}
                      >
                        <RotateCcw size={13} strokeWidth={1.85} />
                        {mutating === memory.memory_id ? "处理中" : "恢复"}
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="lifecycle"
                        disabled={running || Boolean(mutating)}
                        onClick={() => void changeLifecycle(memory.memory_id, "archive")}
                      >
                        <Archive size={13} strokeWidth={1.85} />
                        {mutating === memory.memory_id ? "处理中" : "归档"}
                      </button>
                    )}
                    <button
                      type="button"
                      className={forgetTarget === memory.memory_id ? "confirm" : ""}
                      disabled={running || Boolean(mutating)}
                      onClick={() => void forgetMemory(memory.memory_id)}
                    >
                      <Trash2 size={13} strokeWidth={1.85} />
                      {mutating === memory.memory_id ? "处理中" : forgetTarget === memory.memory_id ? "确认删除" : "删除"}
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
                ? "没有匹配的项目记忆"
                : view === "archived"
                  ? "还没有归档记忆"
                  : "这个项目还没有长期记忆"}
            </strong>
            <span>
              {query.trim()
                ? "换个关键词试试。"
                : view === "archived"
                  ? "低价值且长期未使用的事实会安全进入这里，也可以手动归档。"
                  : "完成一些项目对话后，Loom 会在后台提取、整理并路由值得长期保留的上下文。"}
            </span>
          </div>
        )}
      </div>
    </section>
  );
}
