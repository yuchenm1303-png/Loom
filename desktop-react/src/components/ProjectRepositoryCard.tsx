import { useCallback, useEffect, useRef, useState } from "react";
import { withProjectReadTimeout } from "../state/projectRead";
import { useI18n } from "../i18n";
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
  const zh = useI18n().language === "zh-CN";
  const L = (chinese: string, english: string) => (zh ? chinese : english);
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
  const generation = useRef(0);
  const run = useCallback(async (operation: string, args: Record<string, unknown> = {}) => {
    const request = generation.current;
    const client = Reflect.get(window, "loom") as { call<T>(method: string, args: Record<string, unknown>): Promise<T> } | undefined;
    if (!client) { setError(zh ? "本地主机连接不可用，请重新连接后重试。" : "The local host is not connected; reconnect and try again."); return; }
    setBusy(operation); setError(""); setNotice("");
    onBusyChange(true);
    try {
      const pending = client.call<Repository>(`project/git_${operation}`, { projectId, ...args });
      const result = await (operation === "repository" ? withProjectReadTimeout(pending) : pending);
      if (request !== generation.current) return;
      setState(result);
      if (result.pullRequestUrl) setPrUrl(result.pullRequestUrl);
      if (result.project) setWorktreeProject(result.project);
      if (operation !== "repository") { setNotice(zh ? "操作完成" : "Done"); onChanged(); }
    } catch (cause) { if (request === generation.current) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (request === generation.current) { setBusy(""); onBusyChange(false); } }
  }, [projectId, onChanged, zh]);
  useEffect(() => {
    ++generation.current;
    setState(null);
    setBusy("");
    setPrUrl("");
    setRepo("");
    setBranch("");
    setTitle("");
    setBase("main");
    setBody("");
    setDraft(true);
    setWorktreeProject(null);
    void run("repository");
    return () => { ++generation.current; };
  }, [projectId]);
  const disabled = blocked || Boolean(busy);
  const detached = L("游离 HEAD", "detached HEAD");
  return <section className="project-repository-card" aria-label={L("Git 仓库与协作", "Git repository workflow")}>
    <header><div><Github size={17}/><strong>{L("仓库与协作", "Repository")}</strong></div><button disabled={Boolean(busy)} onClick={() => void run("repository")} aria-label={L("刷新仓库", "Refresh the repository")}><RefreshCw size={14}/></button></header>
    {error && <p role="alert" className="repository-error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {state && !state.isRepo ? <><p>{L("此目录还不是 Git 仓库。", "This folder is not a Git repository yet.")}</p><button disabled={disabled} onClick={() => void run("init")}>{L("初始化 Git 仓库", "Initialise a Git repository")}</button></> : state && <>
      <div className="repository-status"><GitBranch size={15}/><strong>{state.branch || detached}</strong><span>{state.upstream || L("尚未设置上游", "No upstream yet")}</span><span>↑ {state.ahead} · ↓ {state.behind}</span></div>
      {state.url && <a href={state.url} target="_blank" rel="noreferrer">{state.repository} ↗</a>}
      <form onSubmit={e => { e.preventDefault(); void run("bind", { repository: repo }); }}>
        <label>{L("GitHub 仓库", "GitHub repository")}<input aria-label={L("GitHub 仓库", "GitHub repository")} placeholder={state.repository || "owner/repository"} value={repo} onChange={e => setRepo(e.target.value)}/></label>
        <button disabled={disabled || !repo.trim()}>{state.hasRemote ? L("更新关联", "Update link") : L("关联仓库", "Link repository")}</button>
      </form>
      <small>{L("关联会设置 origin。GitHub 授权用于 PR；推送使用本机 Git 凭据。", "Linking sets origin. GitHub authorisation is used for pull requests; pushing uses this machine's Git credentials.")}</small>
      <div className="repository-actions">
        <button disabled={disabled || !state.hasRemote} onClick={() => void run("fetch")}>{L("获取远端", "Fetch")}</button>
        <button disabled={disabled || !state.upstream} onClick={() => void run("pull")}>{L("拉取更新", "Pull")}</button>
        <button disabled={disabled || !state.hasRemote || !state.branch} onClick={() => void run("push")}><Upload size={14}/>{L("推送分支", "Push branch")}</button>
      </div>
      <label>{L("切换分支", "Switch branch")}<select aria-label={L("切换分支", "Switch branch")} value={state.branch || ""} disabled={disabled} onChange={e => void run("switch_branch", {branch:e.target.value})}>
        {!state.branch && <option value="">{detached}</option>}{state.branches.map(name => <option key={name}>{name}</option>)}
      </select></label>
      <label>{L("新分支名称", "New branch name")}<input aria-label={L("新分支名称", "New branch name")} placeholder="codex/my-change" value={branch} onChange={e => setBranch(e.target.value)}/></label>
      <div className="repository-actions"><button disabled={disabled || !branch.trim()} onClick={() => void run("switch_branch", {branch, create:true})}>{L("新建并切换", "Create and switch")}</button>
        <button disabled={disabled || !branch.trim()} onClick={() => void run("create_worktree", {branch})}><FolderGit2 size={14}/>{L("创建独立工作区", "Create a worktree")}</button></div>
      <small>{L("独立工作区从当前提交创建，并自动注册为新项目。", "A worktree starts from the current commit and is added as a new project.")}</small>
      {worktreeProject && <button onClick={() => onNewThread(worktreeProject)}>{L(`进入 ${worktreeProject.name}`, `Open ${worktreeProject.name}`)}</button>}
      <ul className="repository-worktrees">{state.worktrees.map(tree => <li key={tree.path}><span>{tree.branch || detached}</span><code title={tree.path}>{tree.path}</code>{tree.removable && <details><summary>{L("移除工作区", "Remove worktree")}</summary><p>{L("移除干净工作区及项目登记，保留聊天记录。有未提交文件时会拒绝移除。", "Removes a clean worktree and its project entry; conversations are kept. A worktree with uncommitted files is not removed.")}</p><button disabled={disabled} onClick={() => void run("remove_worktree", {path:tree.path})}>{L("移除此工作区", "Remove this worktree")}</button></details>}</li>)}</ul>
      <details><summary>{L("清理已合并分支", "Clean up merged branches")}</summary><p>{L("只删除已合并的本地分支，保留远端分支。", "Deletes merged local branches only; remote branches stay.")}</p>{state.branches.filter(name => name !== state.branch).map(name => <button key={name} disabled={disabled} onClick={() => void run("delete_branch", {branch:name})}>{L(`删除 ${name}`, `Delete ${name}`)}</button>)}</details>
      <details><summary><GitPullRequest size={15}/>{L("创建 PR", "Open a pull request")}</summary>
        {!state.githubConnected && <p>{L("请先在设置 → 连接器中连接 GitHub。", "Connect GitHub first in Settings → Connectors.")}</p>}
        <p>{L("先提交并推送当前分支，再创建合并请求。", "Commit and push the current branch first, then open the pull request.")}</p>
        <label>{L("标题", "Title")}<input value={title} maxLength={256} onChange={e => setTitle(e.target.value)}/></label>
        <label>{L("目标分支", "Base branch")}<input value={base} onChange={e => setBase(e.target.value)}/></label>
        <label>{L("说明", "Description")}<textarea value={body} onChange={e => setBody(e.target.value)}/></label>
        <label className="repository-draft"><input type="checkbox" checked={draft} onChange={e => setDraft(e.target.checked)}/>{L("创建草稿 PR", "Open as a draft")}</label>
        <button disabled={disabled || !state.repository || !state.githubConnected || !title.trim() || !base.trim() || base === state.branch} onClick={() => void run("create_pr", {title,base,body,draft})}>{L("创建 PR", "Open pull request")}</button>
        {prUrl && <a href={prUrl} target="_blank" rel="noreferrer">{L("查看已创建的 PR ↗", "View the pull request ↗")}</a>}
      </details>
    </>}
    {busy && <p role="status">{busy === "repository" ? L("读取仓库…", "Reading the repository…") : L("正在处理…", "Working…")}</p>}
  </section>;
}
