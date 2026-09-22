import {
  BrainCircuit,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Search,
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
  created_at: string;
  updated_at: string;
};

type ProjectMemoryEvidence = {
  evidence_id: string;
  memory_id: string;
  source_session_id: string;
  source_turn_id: string;
  excerpt: string;
  created_at: string;
};

type ProjectMemoryStatus = {
  projectId: string;
  scope: string;
  total: number;
  visible: number;
  categories: Record<string, number>;
  enabled: boolean;
  auto_extract: boolean;
};

type ProjectMemoryReadResult = {
  memory: ProjectMemoryRecord;
  evidence: ProjectMemoryEvidence[];
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

export function ProjectMemoryCard({ projectId, open, running }: ProjectMemoryCardProps) {
  const [status, setStatus] = useState<ProjectMemoryStatus | null>(null);
  const [memories, setMemories] = useState<ProjectMemoryRecord[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<ProjectMemoryReadResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [reading, setReading] = useState("");
  const [forgetTarget, setForgetTarget] = useState("");
  const [forgetting, setForgetting] = useState("");
  const [error, setError] = useState("");

  const loadStatus = useCallback(async () => {
    const result = await window.loom.call<{ memory?: ProjectMemoryStatus }>(
      "project/memory_status",
      { projectId },
    );
    setStatus(result.memory ?? null);
  }, [projectId]);

  const loadList = useCallback(async (search: string) => {
    const needle = search.trim();
    if (needle) {
      const result = await window.loom.call<{ memories?: ProjectMemoryRecord[] }>(
        "project/memory_search",
        { projectId, query: needle, limit: 32 },
      );
      setMemories(Array.isArray(result.memories) ? result.memories : []);
      return;
    }
    const result = await window.loom.call<{ memories?: ProjectMemoryRecord[] }>(
      "project/memory_list",
      { projectId, limit: 200 },
    );
    setMemories(Array.isArray(result.memories) ? result.memories : []);
  }, [projectId]);

  const refresh = useCallback(async () => {
    if (!open || !projectId) return;
    setLoading(true);
    setError("");
    try {
      await Promise.all([loadStatus(), loadList(query)]);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setLoading(false);
    }
  }, [loadList, loadStatus, open, projectId, query]);

  useEffect(() => {
    setQuery("");
    setSelected(null);
    setForgetTarget("");
    if (!open || !projectId) {
      setStatus(null);
      setMemories([]);
      return;
    }
    setLoading(true);
    setError("");
    void Promise.all([loadStatus(), loadList("")])
      .catch((cause) => setError(errorText(cause)))
      .finally(() => setLoading(false));
  }, [loadList, loadStatus, open, projectId]);

  useEffect(() => {
    if (!open || !projectId) return;
    const timer = window.setTimeout(() => {
      void loadList(query).catch((cause) => setError(errorText(cause)));
    }, 220);
    return () => window.clearTimeout(timer);
  }, [loadList, open, projectId, query]);

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
        { projectId, memoryId, evidenceLimit: 24 },
      );
      setSelected(result);
      setForgetTarget("");
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setReading("");
    }
  };

  const forgetMemory = async (memoryId: string) => {
    if (running || forgetting) return;
    if (forgetTarget !== memoryId) {
      setForgetTarget(memoryId);
      return;
    }
    setForgetting(memoryId);
    setError("");
    try {
      const result = await window.loom.call<{ forgotten?: boolean }>(
        "project/memory_forget",
        { projectId, memoryId },
      );
      if (!result.forgotten) throw new Error("这条项目记忆已经不存在或不属于当前项目。");
      setSelected(null);
      setForgetTarget("");
      await Promise.all([loadStatus(), loadList(query)]);
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setForgetting("");
    }
  };

  const categories = status?.categories ?? {};
  const chips = useMemo(
    () => ["decision", "project", "fact", "constraint", "preference"]
      .map((category) => ({ category, count: Number(categories[category] || 0) }))
      .filter((item) => item.count > 0),
    [categories],
  );

  return (
    <section className="project-memory-card">
      <div className="project-card-heading">
        <div>
          <span>Project Memory</span>
          <strong>项目记忆</strong>
        </div>
        <button type="button" onClick={() => void refresh()} disabled={loading} title="刷新项目记忆">
          <RefreshCw size={14} strokeWidth={1.85} className={loading ? "spin" : ""} />
          {loading ? "刷新中" : "刷新"}
        </button>
      </div>

      <p className="project-memory-help">
        自动从这个项目的历史对话中积累长期上下文。这里只显示项目工作区记忆；全局记忆仍在设置里的 Memory 页面管理。
      </p>

      <div className="project-memory-overview">
        <div className="project-memory-total">
          <BrainCircuit size={16} strokeWidth={1.8} />
          <span>已保存</span>
          <strong>{status?.total ?? memories.length}</strong>
          <small>{status?.enabled === false ? "Memory 已关闭，现有记忆仍可查看" : "advisory · 当前指令优先"}</small>
        </div>
        <div className="project-memory-category-list" aria-label="Project memory categories">
          {chips.length ? chips.map((item) => (
            <span className={`project-memory-category ${item.category}`} key={item.category}>
              {categoryLabel(item.category)}
              <b>{item.count}</b>
            </span>
          )) : <span className="project-memory-category empty">暂无分类</span>}
        </div>
      </div>

      {running ? (
        <div className="project-memory-note">
          项目里有任务正在运行。记忆仍可查看，但需要等任务结束后才能删除。
        </div>
      ) : null}

      {error ? <div className="project-memory-error">{error}</div> : null}

      <label className="project-memory-search">
        <Search size={14} strokeWidth={1.9} />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索决策、事实、约束…"
          aria-label="Search project memory"
        />
        {query ? (
          <button type="button" onClick={() => setQuery("")} aria-label="Clear project memory search">
            <X size={13} strokeWidth={1.9} />
          </button>
        ) : null}
      </label>

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
                    {relativeTime(memory.updated_at)}
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
                  </div>
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
                  <div className="project-memory-detail-actions">
                    <span>{forgetTarget === memory.memory_id ? "再次点击确认删除这条项目记忆" : "删除只影响这条长期记忆，不会删除原对话"}</span>
                    <button
                      type="button"
                      className={forgetTarget === memory.memory_id ? "confirm" : ""}
                      disabled={running || Boolean(forgetting)}
                      onClick={() => void forgetMemory(memory.memory_id)}
                    >
                      <Trash2 size={13} strokeWidth={1.85} />
                      {forgetting === memory.memory_id ? "删除中" : forgetTarget === memory.memory_id ? "确认删除" : "删除"}
                    </button>
                  </div>
                </div>
              ) : null}
            </article>
          );
        }) : (
          <div className="project-memory-empty">
            <BrainCircuit size={19} strokeWidth={1.7} />
            <strong>{query.trim() ? "没有匹配的项目记忆" : "这个项目还没有长期记忆"}</strong>
            <span>{query.trim() ? "换个关键词试试。" : "完成一些项目对话后，Loom 会在后台提取值得长期保留的上下文。"}</span>
          </div>
        )}
      </div>
    </section>
  );
}
