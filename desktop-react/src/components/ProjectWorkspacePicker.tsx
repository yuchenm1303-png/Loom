import type { ProjectRecord } from "../types/loom";

export interface WorkspaceState {
  path?: string; status?: "ready" | "not_git" | "unborn" | "detached" | "nested" | "invalid" | "unavailable";
  repositoryRoot?: string;
}

export function ProjectWorkspacePicker({ projects, workspace, locked, lang, branch, onSelect, onPrepare }: {
  projects: ProjectRecord[]; workspace?: WorkspaceState; locked: boolean; lang: "zh" | "en"; branch: string;
  onSelect?(id: string): void; onPrepare?(action: "initialize" | "branch"): void;
}) {
  const zh = lang === "zh";
  const normalize = (path = "") => path.replace(/\\/g, "/").replace(/\/$/, "");
  const selected = projects.find(item => normalize(item.root) === normalize(workspace?.path))?.id || "";
  const status = workspace?.status;
  const initialize = status === "not_git" || status === "unborn";
  return <div className="pf-workspace-controls">
    <label>
      <span>{zh ? "左侧已有项目" : "Existing projects"}</span>
      <select aria-label={zh ? "选择已有项目" : "Choose existing project"} value={selected} disabled={locked || !onSelect}
              onChange={event => { if (event.target.value) onSelect?.(event.target.value); }}>
        <option value="">{zh ? "选择项目…" : "Choose a project…"}</option>
        {projects.map(project => <option key={project.id} value={project.id}>{project.name} · {project.root}</option>)}
      </select>
    </label>
    {!locked && (initialize || status === "detached") ? <details className="pf-workspace-setup" key={`${workspace?.path}:${status}`}>
      <summary>{initialize ? (zh ? "准备 Git 仓库" : "Prepare Git repository") : (zh ? "创建工作分支" : "Create work branch")}</summary>
      <p>{initialize
        ? (zh ? "将创建本地 Git 仓库和一个空的初始提交。现有文件不会自动提交或上传；已有代码需查看并提交后才会被建造任务使用。" : "Create local Git history with an empty initial commit. Existing files are not committed or uploaded; review and commit them before building on them.")
        : (zh ? `将在当前提交上创建 ${branch}，保留现有文件，不切换到其他版本。` : `Create ${branch} at the current commit, preserving existing files.`)}</p>
      <button type="button" className="pf-folder-picker" onClick={() => onPrepare?.(initialize ? "initialize" : "branch")} disabled={!onPrepare}>
        {initialize ? (zh ? "确认初始化（不提交现有文件）" : "Initialize (exclude existing files)") : (zh ? "确认创建分支" : "Create this branch")}
      </button>
    </details> : null}
    {!locked && status === "nested" ? <p>{zh ? `请选择 Git 根目录：${workspace?.repositoryRoot}` : `Choose the Git root: ${workspace?.repositoryRoot}`}</p> : null}
    {!locked && (status === "invalid" || status === "unavailable") ? <p>{zh ? "目录不可用或 Git 信息损坏，请重新选择文件夹。" : "Folder unavailable or Git metadata invalid; choose another folder."}</p> : null}
    {locked ? <p>{zh ? "工作期间目录保持不变；其他项目请新建会话。" : "The folder stays fixed while working; use a new conversation for another project."}</p> : null}
  </div>;
}
