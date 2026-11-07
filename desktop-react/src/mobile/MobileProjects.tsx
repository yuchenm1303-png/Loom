import { useEffect, useState } from "react";
import {
  FolderOpen,
  Plus,
  RotateCw,
  MessagesSquare,
  Blocks,
} from "../components/icons";
import type { useLoom } from "../state/useLoom";
import type { ProjectRecord, ThreadRecord } from "../types/loom";
import { inProject, searchThreads } from "./mobileProjectState";
import { withProjectReadTimeout } from "../state/projectRead";

type Loom = ReturnType<typeof useLoom>;
export function ProjectReadStatus({
  loom,
  online,
}: {
  loom: Loom;
  online: boolean;
}) {
  return (
    <div className="m-project-status">
      {loom.projectsError ? (
        <p className="m-alert" role="alert">
          项目刷新失败：{loom.projectsError}
          {loom.projects.length ? "。仍显示上次读取的项目。" : ""}
        </p>
      ) : null}
      <button
        type="button"
        className="m-inline-button"
        disabled={!online || loom.projectsLoading}
        onClick={() => void loom.refreshProjects()}
      >
        <RotateCw size={16} />
        {loom.projectsLoading ? "正在读取项目…" : "刷新项目"}
      </button>
    </div>
  );
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
  return (
    <div className="m-project-library">
      <ProjectReadStatus loom={loom} online={online} />
      <label className="m-field">
        搜索项目或对话
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="项目名、对话标题或目录"
        />
      </label>
      {project ? (
        <>
          <button
            type="button"
            className="m-inline-button"
            onClick={() => setSelected("")}
          >
            返回全部项目
          </button>
          <section className="m-card">
            <h2>{project.name}</h2>
            <p className="m-path">{project.root}</p>
            {project.instructions ? (
              <details>
                <summary>项目说明</summary>
                <p className="m-prewrap">{project.instructions}</p>
              </details>
            ) : null}
            <details>
              <summary>编辑项目名称和说明</summary>
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
                className="m-button"
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
            </details>
            {error ? (
              <p className="m-alert" role="alert">
                {error}
              </p>
            ) : null}
            <div className="m-actions">
              <button
                type="button"
                className="m-button"
                disabled={!online}
                onClick={() => onBegin("chat", project)}
              >
                <MessagesSquare size={16} />
                新对话
              </button>
              <button
                type="button"
                className="m-button"
                disabled={!online}
                onClick={() => onBegin("build", project)}
              >
                <Blocks size={16} />
                新建造
              </button>
            </div>
          </section>
          <section className="m-group">
            <h2>项目中的对话 · {shown.length}</h2>
            <button
              type="button"
              className="m-inline-button"
              disabled={!online || threadLoading}
              onClick={() => setRefresh((value) => value + 1)}
            >
              {threadLoading ? "正在读取…" : "刷新项目对话"}
            </button>
            {threadError ? (
              <p className="m-alert" role="alert">
                {threadError}
              </p>
            ) : null}
            <div className="m-rows">
              {shown.map((thread) => (
                <button
                  type="button"
                  className="m-row"
                  key={thread.id}
                  onClick={() => onOpen(thread)}
                >
                  <span className="m-row-icon">
                    {thread.weave ? (
                      <Blocks size={18} />
                    ) : (
                      <MessagesSquare size={18} />
                    )}
                  </span>
                  <span className="m-row-text">
                    <strong>{thread.title || "新对话"}</strong>
                    <small>
                      {thread.archived
                        ? "已归档"
                        : thread.status === "waiting_approval"
                          ? "等待批准"
                          : thread.status === "running"
                            ? "运行中"
                            : "查看对话"}
                    </small>
                  </span>
                </button>
              ))}
            </div>
            {!shown.length && !threadLoading && !threadError ? (
              <p className="m-note">
                {query
                  ? "没有匹配的对话"
                  : "这个项目还没有对话，可以从这里开始。"}
              </p>
            ) : null}
            {(currentProjectThreads?.length ?? 0) >= 200 ? (
              <p className="m-note">显示项目中最近 200 个对话。</p>
            ) : null}
          </section>
        </>
      ) : (
        <>
          <section className="m-group">
            <h2>电脑上的项目 · {loom.projects.length}</h2>
            <div className="m-rows">
              {loom.projects
                .filter(
                  (item) =>
                    !query.trim() ||
                    `${item.name}\n${item.root}`
                      .toLowerCase()
                      .includes(query.trim().toLowerCase()),
                )
                .map((item) => (
                  <button
                    type="button"
                    className="m-choice"
                    key={item.id}
                    onClick={() => {
                      setSelected(item.id);
                      setQuery("");
                    }}
                  >
                    <FolderOpen size={20} />
                    <span>
                      <strong>{item.name}</strong>
                      <small className="m-path">{item.root}</small>
                    </span>
                    <small>
                      {item.threadCount ??
                        threads.filter((thread) => inProject(thread, item))
                          .length}{" "}
                      个对话
                    </small>
                  </button>
                ))}
            </div>
          </section>
          {!loom.projects.length &&
          !loom.projectsLoading &&
          !loom.projectsError ? (
            <p className="m-note">
              电脑尚未登记项目。可以在下方添加电脑上的工作目录。
            </p>
          ) : null}
          <button
            type="button"
            className="m-inline-button"
            disabled={!online}
            aria-expanded={adding}
            onClick={() => setAdding(!adding)}
          >
            <Plus size={16} />
            添加电脑上的项目
          </button>
          {adding ? (
            <form
              className="m-card"
              onSubmit={(event) => {
                event.preventDefault();
                void submit();
              }}
            >
              <label className="m-field">
                项目名称
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="可选，默认使用目录名"
                  disabled={busy}
                />
              </label>
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
                {busy ? "正在添加…" : "添加项目"}
              </button>
            </form>
          ) : null}
        </>
      )}
    </div>
  );
}
