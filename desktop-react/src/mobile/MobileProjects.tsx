import { useEffect, useState } from "react";
import { Roll } from "../components/RollText";
import { MorphText } from "./MotionRows";
import { AnimatedFold } from "../motion/AnimatedFold";
import {
  Blocks,
  ChevronLeft,
  ChevronRight,
  FolderOpen,
  FolderPlus,
  MessagesSquare,
  RotateCw,
  Search,
} from "../components/icons";
import type { useLoom } from "../state/useLoom";
import type { ProjectRecord, ThreadRecord } from "../types/loom";
import { inProject, searchThreads } from "./mobileProjectState";
import { withProjectReadTimeout } from "../state/projectRead";
import { Disclosure } from "./Disclosure";

type Loom = ReturnType<typeof useLoom>;

/** The phone's one search box: a pill with its glass, no label above it. */
export function SearchField({
  value,
  onChange,
  label,
  placeholder,
}: {
  value: string;
  onChange(value: string): void;
  label: string;
  placeholder: string;
}) {
  return (
    <label className="m-search">
      <Search size={17} strokeWidth={1.9} aria-hidden="true" />
      <input
        type="search"
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        enterKeyHint="search"
      />
    </label>
  );
}

export function ProjectRefresh({ loom, online }: { loom: Loom; online: boolean }) {
  return (
    <button
      type="button"
      className="m-icon-button"
      aria-label="刷新项目"
      disabled={!online || loom.projectsLoading}
      onClick={() => void loom.refreshProjects()}
    >
      <RotateCw size={17} strokeWidth={1.75} spinning={loom.projectsLoading} />
    </button>
  );
}

/** Only speaks when the last read failed; refreshing lives beside the list it refreshes. */
export function ProjectReadStatus({
  loom,
}: {
  loom: Loom;
  online: boolean;
}) {
  if (!loom.projectsError) return null;
  return (
    <div className="m-project-status">
      <p className="m-alert" role="alert">
        项目刷新失败：{loom.projectsError}
        {loom.projects.length ? "。仍显示上次读取的项目。" : ""}
      </p>
    </div>
  );
}

function threadState(thread: ThreadRecord): { text: string; tone?: "wait" | "run" } {
  if (thread.archived) return { text: "已归档" };
  if (thread.status === "waiting_approval") return { text: "等待批准", tone: "wait" };
  if (thread.status === "running") return { text: "运行中", tone: "run" };
  return { text: thread.weave ? "建造" : "对话" };
}

export function MobileProjects({
  loom,
  online,
  threads,
  onOpen,
  onBegin,
}: {
  loom: Loom;
  online: boolean;
  threads: ThreadRecord[];
  onOpen(thread: ThreadRecord): void;
  onBegin(mode: "chat" | "build", project: ProjectRecord): void;
}) {
  const [selected, setSelected] = useState("");
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [root, setRoot] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const project = loom.projects.find((item) => item.id === selected);
  const [projectThreads, setProjectThreads] = useState<{ projectId: string; threads: ThreadRecord[] } | null>(
    null,
  );
  const [threadError, setThreadError] = useState("");
  const [threadLoading, setThreadLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [projectName, setProjectName] = useState("");
  const [instructions, setInstructions] = useState("");
  useEffect(() => {
    setProjectName(project?.name || "");
    setInstructions(project?.instructions || "");
    setError("");
  }, [project?.id]);
  useEffect(() => {
    setProjectThreads((current) => project && current?.projectId === project.id ? current : null);
    setThreadError("");
    if (!project || !online) { setThreadLoading(false); return; }
    let cancelled = false;
    setThreadLoading(true);
    void withProjectReadTimeout(
      window.loom.call<{ threads: ThreadRecord[] }>("thread/list", {
        view: "all",
        projectId: project.id,
        limit: 200,
      }),
    )
      .then((result) => {
        if (!Array.isArray(result.threads))
          throw new Error("电脑返回的对话列表不完整");
        if (!cancelled) setProjectThreads({ projectId: project.id, threads: result.threads });
      })
      .catch((cause) => {
        if (!cancelled)
          setThreadError(
            cause instanceof Error ? cause.message : String(cause),
          );
      })
      .finally(() => {
        if (!cancelled) setThreadLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [project?.id, online, refresh]);
  const submit = async () => {
    if (busy || !root.trim() || !online) return;
    setBusy(true);
    setError("");
    try {
      const created = await loom.createProject(root.trim(), name.trim());
      setSelected(created.id);
      setAdding(false);
      setRoot("");
      setName("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };
  const currentProjectThreads = projectThreads?.projectId === project?.id ? projectThreads?.threads : null;
  const shown = searchThreads(currentProjectThreads || threads, query, project);
  if (project)
    return (
      <div className="m-project-library m-project-view" key={project.id}>
        <div className="m-group-head m-back-row">
          <button
            type="button"
            className="m-inline-button m-back"
            onClick={() => setSelected("")}
          >
            <ChevronLeft size={18} strokeWidth={2} />
            全部项目
          </button>
          <ProjectRefresh loom={loom} online={online} />
        </div>
        <ProjectReadStatus loom={loom} online={online} />
        <section className="m-card">
          <div className="m-card-head">
            <span className="m-tile is-accent is-large" aria-hidden="true">
              <FolderOpen size={22} strokeWidth={1.75} />
            </span>
            <div>
              <h2>{project.name}</h2>
              <p className="m-path">{project.root}</p>
            </div>
          </div>
          <div className="m-actions">
            <button
              type="button"
              className="m-button is-tint"
              disabled={!online}
              onClick={() => onBegin("chat", project)}
            >
              <MessagesSquare size={17} strokeWidth={1.9} />
              新对话
            </button>
            <button
              type="button"
              className="m-button"
              disabled={!online}
              onClick={() => onBegin("build", project)}
            >
              <Blocks size={17} strokeWidth={1.9} />
              新建造
            </button>
          </div>
          <div className="m-disclosures">
            {project.instructions ? (
              <Disclosure title="项目说明">
                <p className="m-prewrap">{project.instructions}</p>
              </Disclosure>
            ) : null}
            <Disclosure title="编辑项目名称和说明">
              <label className="m-field">
                项目名称
                <input
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                  disabled={busy}
                />
              </label>
              <label className="m-field">
                项目说明
                <textarea
                  rows={5}
                  value={instructions}
                  onChange={(event) => setInstructions(event.target.value)}
                  disabled={busy}
                />
              </label>
              <button
                type="button"
                className="m-button is-tint is-block"
                disabled={!online || busy || !projectName.trim()}
                onClick={() => {
                  setBusy(true);
                  setError("");
                  void (async () => {
                    if (projectName.trim() !== project.name)
                      await loom.renameProject(project.id, projectName.trim());
                    await loom.setProjectInstructions(project.id, instructions);
                  })()
                    .catch((cause) =>
                      setError(
                        cause instanceof Error ? cause.message : String(cause),
                      ),
                    )
                    .finally(() => setBusy(false));
                }}
              >
                保存项目设置
              </button>
            </Disclosure>
          </div>
          {error ? (
            <p className="m-alert" role="alert">
              {error}
            </p>
          ) : null}
        </section>
        <section className="m-group">
          <div className="m-group-head">
            <h2>项目中的对话 · {shown.length}</h2>
            <button
              type="button"
              className="m-icon-button"
              aria-label="刷新项目对话"
              disabled={!online || threadLoading}
              onClick={() => setRefresh((value) => value + 1)}
            >
              <RotateCw size={17} strokeWidth={1.75} spinning={threadLoading} />
            </button>
          </div>
          {(currentProjectThreads?.length ?? 0) > 6 || query ? (
            <SearchField value={query} onChange={setQuery} label="搜索项目中的对话" placeholder="对话标题" />
          ) : null}
          {threadError ? (
            <p className="m-alert" role="alert">
              {threadError}
            </p>
          ) : null}
          {shown.length ? (
            <div className="m-rows">
              {shown.map((thread) => {
                const state = threadState(thread);
                return (
                  <button
                    type="button"
                    className="m-row"
                    key={thread.id}
                    onClick={() => onOpen(thread)}
                  >
                    <span className="m-row-icon" aria-hidden="true">
                      {thread.weave ? (
                        <Blocks size={19} strokeWidth={1.75} />
                      ) : (
                        <MessagesSquare size={19} strokeWidth={1.75} />
                      )}
                    </span>
                    <span className="m-row-text">
                      <strong><MorphText text={thread.title || "新对话"} /></strong>
                    </span>
                    {state.tone ? (
                      <span className={`m-badge is-${state.tone}`}><MorphText text={state.text} /></span>
                    ) : (
                      <small><MorphText text={state.text} /></small>
                    )}
                  </button>
                );
              })}
            </div>
          ) : null}
          {!shown.length && !threadLoading && !threadError ? (
            <p className="m-note">
              {query
                ? "没有匹配的对话"
                : "这个项目还没有对话，可以从上面开始。"}
            </p>
          ) : null}
          {(currentProjectThreads?.length ?? 0) >= 200 ? (
            <p className="m-note">显示项目中最近 200 个对话。</p>
          ) : null}
        </section>
      </div>
    );
  const projects = loom.projects.filter(
    (item) =>
      !query.trim() ||
      `${item.name}\n${item.root}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  return (
    <div className="m-project-library">
      <div className="m-toolbar-row">
        <SearchField value={query} onChange={setQuery} label="搜索项目或对话" placeholder="项目名或目录" />
      </div>
      <ProjectReadStatus loom={loom} online={online} />
      <section className="m-group">
        <div className="m-group-head">
          <h2>电脑上的项目 · {loom.projects.length}</h2>
          <ProjectRefresh loom={loom} online={online} />
        </div>
        <div className="m-rows">
          {projects.map((item) => (
            <button
              type="button"
              className="m-choice"
              key={item.id}
              onClick={() => {
                setSelected(item.id);
                setQuery("");
              }}
            >
              <span className="m-tile is-accent" aria-hidden="true">
                <FolderOpen size={19} strokeWidth={1.75} />
              </span>
              <span>
                <strong>{item.name}</strong>
                <small className="m-path">{item.root}</small>
              </span>
              <em>
                {item.threadCount ??
                  threads.filter((thread) => inProject(thread, item)).length}{" "}
                个对话
              </em>
              <ChevronRight className="m-chevron" size={18} strokeWidth={1.75} />
            </button>
          ))}
          <button
            type="button"
            className="m-choice"
            disabled={!online}
            aria-expanded={adding}
            onClick={() => setAdding(!adding)}
          >
            <span className="m-tile" aria-hidden="true">
              <FolderPlus size={19} strokeWidth={1.75} />
            </span>
            <span>
              <strong>添加电脑上的项目</strong>
              <small>登记电脑上的一个工作目录</small>
            </span>
          </button>
        </div>
        {query.trim() && !projects.length ? <p className="m-note">没有匹配的项目</p> : null}
        {!loom.projects.length &&
        !loom.projectsLoading &&
        !loom.projectsError ? (
          <p className="m-note">
            电脑尚未登记项目。可以在上方添加电脑上的工作目录。
          </p>
        ) : null}
      </section>
      <AnimatedFold open={adding}>
        <form
          className="m-card"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <h2>添加项目</h2>
          <label className="m-field">
            电脑上的完整目录
            <input
              value={root}
              onChange={(event) => setRoot(event.target.value)}
              placeholder="例如 C:\Projects\MyApp"
              autoCapitalize="off"
              autoCorrect="off"
              required
              disabled={busy}
            />
          </label>
          <label className="m-field">
            项目名称
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="可选，默认使用目录名"
              disabled={busy}
            />
          </label>
          <p className="m-note">
            这是电脑的工作目录，手机不需要有同名文件夹。
          </p>
          {error ? (
            <p className="m-alert" role="alert">
              {error}
            </p>
          ) : null}
          <button
            type="submit"
            className="m-button is-primary"
            disabled={busy || !online || !root.trim()}
          >
            <Roll text={busy ? "正在添加…" : "添加项目"} />
          </button>
        </form>
      </AnimatedFold>
    </div>
  );
}
