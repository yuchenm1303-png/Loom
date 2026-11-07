import { createContext, useContext, useLayoutEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { FolderOpen, FileText, ListChecks, Bot, Gauge, X } from "../components/icons";

export type BuildBranch = "project" | "files" | "tasks" | "agents" | "budget";
export const BuildBranchContext = createContext<{ branch: BuildBranch | null; dock: HTMLElement | null }>({ branch: null, dock: null });
export function branchFor(title: string): BuildBranch {
  if (/目录|Git/.test(title)) return "project";
  if (/参考资料/.test(title)) return "files";
  if (/任务|验收|指导/.test(title)) return "tasks";
  if (/Agent/.test(title)) return "agents";
  return "budget";
}
export function BranchCard({ title, urgent, children }: { title: string; urgent?: boolean; children: ReactNode }) {
  const { branch, dock } = useContext(BuildBranchContext);
  if (urgent && dock) return createPortal(children, dock);
  return branch === branchFor(title) ? children : null;
}
const entries = [
  ["project", "项目", FolderOpen], ["files", "资料", FileText], ["tasks", "任务", ListChecks], ["agents", "Agent", Bot], ["budget", "预算", Gauge],
] as const;
export function BuildBranches({ dock, branch, onBranch, summary, children }: {
  dock: HTMLElement | null; branch: BuildBranch | null; onBranch(branch: BuildBranch | null): void; summary: string; children: ReactNode;
}) {
  useLayoutEffect(() => {
    if (!branch) return;
    const back = (event: Event) => {
      if (document.querySelector('.m-sheet-host:not([inert]), .user-message-image-lightbox:not([inert])')) return;
      event.preventDefault(); event.stopImmediatePropagation(); onBranch(null);
    };
    window.addEventListener("loom:mobile-back", back, true);
    return () => window.removeEventListener("loom:mobile-back", back, true);
  }, [branch, onBranch]);
  return <BuildBranchContext.Provider value={{ dock, branch }}>
    {dock ? createPortal(<div className="m-branches">
      <button type="button" className="m-build-summary" aria-label="查看建造状态与预算" onClick={() => onBranch(branch === "budget" ? null : "budget")}>{summary}</button>
      <nav className="m-branch-ports" aria-label="建造支路">{entries.map(([key, label, Icon]) => <button type="button" key={key} aria-expanded={branch === key} aria-controls="mobile-build-branch" onClick={() => onBranch(branch === key ? null : key)}><Icon size={17} /><span>{label}</span></button>)}</nav>
    </div>, dock) : null}
    <div id="mobile-build-branch" className="m-branch-content" data-branch={branch || "closed"}>
      {branch ? <button type="button" className="m-inline-button m-branch-close" onClick={() => onBranch(null)}><X size={16} />收起支路，返回对话</button> : null}
      {branch ? <p className="m-branch-intro">{{ project: "当前工作目录与仓库准备。其他项目可从项目页开始。", files: "从输入框旁添加附件，发送给对话或作为建造参考资料。", tasks: "任务、验收与后续指导会显示在这里。", agents: "执行中的 Agent 和分工会显示在这里；待审批事项会直接浮出。", budget: "方案出现后可确认预算与并发；运行后可暂停、恢复和调整预算。" }[branch]}</p> : null}
      {children}
    </div>
  </BuildBranchContext.Provider>;
}
