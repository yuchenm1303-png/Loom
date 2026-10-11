import { useEffect, useState } from "react";
import { Folder, GitBranch, Laptop, GitPullRequest } from "./icons";
import type { ProjectRecord } from "../types/loom";
import "./project-git-bar.css";
import { startVisiblePolling } from "../visiblePolling";

export function ProjectGitBar({ project, onOpen }: { project: ProjectRecord; onOpen(): void }) {
  const [repository, setRepository] = useState<{isRepo:boolean;branch:string;isWorktree:boolean;additions:number;deletions:number;ahead:number;behind:number;repository:string}|null>(null);
  useEffect(() => {
    let live = true;
    setRepository(null);
    const refresh = async () => {
      const value = await window.loom.call<typeof repository>("project/git_repository", {projectId:project.id});
      if (live) setRepository(value);
    };
    const stop = startVisiblePolling(refresh, 15000);
    return () => { live = false; stop(); };
  }, [project.id]);
  return <div className="project-git-bar" aria-label="当前项目与 Git">
    <span><Laptop size={13}/>Local</span>
    <button onClick={onOpen} title={project.root}><Folder size={13}/>{project.name}</button>
    {repository?.isRepo && <button onClick={onOpen}><GitBranch size={13}/>{repository.branch || "游离 HEAD"}<small>↑{repository.ahead} ↓{repository.behind}</small></button>}
    {repository?.isWorktree ? <span>worktree</span> : null}
    {repository && (repository.additions > 0 || repository.deletions > 0) && <button onClick={onOpen} title="已跟踪文件相对 HEAD 的变更"><span className="git-additions">+{repository.additions}</span><span className="git-deletions">−{repository.deletions}</span></button>}
    <button className="project-git-pr" onClick={onOpen}><GitPullRequest size={13}/>仓库 / PR</button>
  </div>;
}
