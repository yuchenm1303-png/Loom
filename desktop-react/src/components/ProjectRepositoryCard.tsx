import { useCallback, useEffect, useState } from "react";
import { GitBranch, Github, RefreshCw, Upload, GitPullRequest, FolderGit2 } from "./icons";
import "./project-repository.css";
import type { ProjectRecord } from "../types/loom";

interface Repository {
  isRepo: boolean; repository: string; url?: string; branch?: string;
  branches: string[]; worktrees: { path: string; branch: string; removable?:boolean }[];
  hasRemote?: boolean; upstream?: string; ahead?: number; behind?: number; githubConnected?: boolean;
  pullRequestUrl?: string;
  project?: ProjectRecord;
}
export function ProjectRepositoryCard({ projectId, blocked, onChanged, onNewThread, onBusyChange }: { projectId: string; blocked: boolean; onChanged(): void; onNewThread(project:ProjectRecord): void; onBusyChange(busy:boolean): void }) {
  const [state, setState] = useState<Repository | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("");
  const [title, setTitle] = useState("");
  const [base, setBase] = useState("main");
  const [body, setBody] = useState("");
  const [draft, setDraft] = useState(true);
  const [prUrl, setPrUrl] = useState("");
  const [notice, setNotice] = useState("");
  const [worktreeProject, setWorktreeProject] = useState<ProjectRecord | null>(null);
  const run = useCallback(async (operation: string, args: Record<string, unknown> = {}) => {
    const client = Reflect.get(window, "loom") as { call<T>(method: string, args: Record<string, unknown>): Promise<T> } | undefined;
    if (!client) return;
    setBusy(operation); setError(""); setNotice("");
    onBusyChange(true);
    try {
      const result = await client.call<Repository>(`project/git_${operation}`, { projectId, ...args });
      setState(result);
      if (result.pullRequestUrl) setPrUrl(result.pullRequestUrl);
      if (result.project) setWorktreeProject(result.project);
      if (operation !== "repository") { setNotice("操作完成"); onChanged(); }
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(""); onBusyChange(false); }
  }, [projectId, onChanged]);
  useEffect(() => { setState(null); setPrUrl(""); setRepo(""); setBranch(""); setWorktreeProject(null); void run("repository"); }, [projectId]);
  const disabled = blocked || Boolean(busy);
  return <section className="project-repository-card" aria-label="Git repository workflow">
    <header><div><Github size={17}/><strong>仓库与协作</strong></div><button disabled={Boolean(busy)} onClick={() => void run("repository")} aria-label="刷新仓库"><RefreshCw size={14}/></button></header>
    {error && <p role="alert" className="repository-error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {state && !state.isRepo ? <><p>此目录还不是 Git 仓库。</p><button disabled={disabled} onClick={() => void run("init")}>初始化 Git 仓库</button></> : state && <>
      <div className="repository-status"><GitBranch size={15}/><strong>{state.branch || "游离 HEAD"}</strong><span>{state.upstream || "尚未设置上游"}</span><span>↑ {state.ahead} · ↓ {state.behind}</span></div>
      {state.url && <a href={state.url} target="_blank" rel="noreferrer">{state.repository} ↗</a>}
      <form onSubmit={e => { e.preventDefault(); void run("bind", { repository: repo }); }}>
        <label>GitHub 仓库<input aria-label="GitHub 仓库" placeholder={state.repository || "owner/repository"} value={repo} onChange={e => setRepo(e.target.value)}/></label>
        <button disabled={disabled || !repo.trim()}>{state.hasRemote ? "更新关联" : "关联仓库"}</button>
      </form>
      <small>关联会设置 origin。GitHub 授权用于 PR；推送使用本机 Git 凭据。</small>
      <div className="repository-actions">
        <button disabled={disabled || !state.hasRemote} onClick={() => void run("fetch")}>获取远端</button>
        <button disabled={disabled || !state.upstream} onClick={() => void run("pull")}>拉取更新</button>
        <button disabled={disabled || !state.hasRemote || !state.branch} onClick={() => void run("push")}><Upload size={14}/>推送分支</button>
      </div>
      <label>切换分支<select aria-label="切换分支" value={state.branch || ""} disabled={disabled} onChange={e => void run("switch_branch", {branch:e.target.value})}>
        {!state.branch && <option value="">游离 HEAD</option>}{state.branches.map(name => <option key={name}>{name}</option>)}
      </select></label>
      <label>新分支名称<input aria-label="新分支名称" placeholder="codex/my-change" value={branch} onChange={e => setBranch(e.target.value)}/></label>
      <div className="repository-actions"><button disabled={disabled || !branch.trim()} onClick={() => void run("switch_branch", {branch, create:true})}>新建并切换</button>
        <button disabled={disabled || !branch.trim()} onClick={() => void run("create_worktree", {branch})}><FolderGit2 size={14}/>创建独立工作区</button></div>
      <small>独立工作区从当前提交创建，并自动注册为新项目。</small>
      {worktreeProject && <button onClick={() => onNewThread(worktreeProject)}>进入 {worktreeProject.name}</button>}
      <ul className="repository-worktrees">{state.worktrees.map(tree => <li key={tree.path}><span>{tree.branch || "游离 HEAD"}</span><code title={tree.path}>{tree.path}</code>{tree.removable && <details><summary>移除工作区</summary><p>移除干净工作区及项目登记，保留聊天记录。有未提交文件时会拒绝移除。</p><button disabled={disabled} onClick={() => void run("remove_worktree", {path:tree.path})}>移除此工作区</button></details>}</li>)}</ul>
      <details><summary>清理已合并分支</summary><p>只删除已合并的本地分支，保留远端分支。</p>{state.branches.filter(name => name !== state.branch).map(name => <button key={name} disabled={disabled} onClick={() => void run("delete_branch", {branch:name})}>删除 {name}</button>)}</details>
      <details><summary><GitPullRequest size={15}/>创建 PR</summary>
        {!state.githubConnected && <p>请先在设置 → 连接器中连接 GitHub。</p>}
        <p>先提交并推送当前分支，再创建合并请求。</p>
        <label>标题<input value={title} maxLength={256} onChange={e => setTitle(e.target.value)}/></label>
        <label>目标分支<input value={base} onChange={e => setBase(e.target.value)}/></label>
        <label>说明<textarea value={body} onChange={e => setBody(e.target.value)}/></label>
        <label className="repository-draft"><input type="checkbox" checked={draft} onChange={e => setDraft(e.target.checked)}/>创建草稿 PR</label>
        <button disabled={disabled || !state.repository || !state.githubConnected || !title.trim() || !base.trim() || base === state.branch} onClick={() => void run("create_pr", {title,base,body,draft})}>创建 PR</button>
        {prUrl && <a href={prUrl} target="_blank" rel="noreferrer">查看已创建的 PR ↗</a>}
      </details>
    </>}
    {busy && <p role="status">{busy === "repository" ? "读取仓库…" : "正在处理…"}</p>}
  </section>;
}
