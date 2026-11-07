import {
  createContext,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import {
  FolderOpen,
  FileText,
  ListChecks,
  Bot,
  Gauge,
  X,
  Sparkles,
  History,
  BookMarked,
  Compass,
  Check,
} from "../components/icons";
import { useReducedMotion } from "../motion/useReducedMotion";
export type BuildBranch =
  | "project"
  | "files"
  | "tasks"
  | "agents"
  | "budget"
  | "model"
  | "usage"
  | "scope"
  | "knowledge"
  | "history"
  | "delivery";
export const BuildBranchContext = createContext<{
  branch: BuildBranch | null;
  dock: HTMLElement | null;
}>({ branch: null, dock: null });
export function branchFor(title: string): BuildBranch {
  if (/历史/.test(title)) return "history";
  if (/交付/.test(title)) return "delivery";
  if (/工作范围/.test(title)) return "scope";
  if (/知识/.test(title)) return "knowledge";
  if (/模型/.test(title)) return "model";
  if (/额度/.test(title)) return "usage";
  if (/目录|Git/.test(title)) return "project";
  if (/参考资料/.test(title)) return "files";
  if (/任务|验收|指导/.test(title)) return "tasks";
  if (/Agent/.test(title)) return "agents";
  return "budget";
}
export function BranchCard({
  title,
  urgent,
  category,
  children,
}: {
  title: string;
  urgent?: boolean;
  category?: BuildBranch;
  children: ReactNode;
}) {
  const { branch, dock } = useContext(BuildBranchContext);
  if (urgent && dock) return createPortal(children, dock);
  return branch === (category || branchFor(title)) ? children : null;
}
const entries = [
  ["project", "项目", FolderOpen],
  ["files", "资料", FileText],
  ["tasks", "任务", ListChecks],
  ["agents", "Agent", Bot],
  ["budget", "运行", Gauge],
  ["model", "模型", Sparkles],
  ["usage", "额度", Gauge],
  ["scope", "范围", Compass],
  ["knowledge", "知识", BookMarked],
  ["history", "历史", History],
  ["delivery", "交付", Check],
] as const;
const notes: Record<BuildBranch, string> = {
  project: "当前工作目录与仓库准备。",
  files: "从输入框旁添加附件，发送给对话或作为建造参考资料。",
  tasks: "任务、验收与后续指导。",
  agents: "执行 Agent 与分工；审批会直接浮出。",
  budget: "启动方案、预算、并发与暂停恢复。",
  model: "当前对话模型与推理设置。",
  usage: "供应商报告的额度，与本次建造预算分别计算。",
  scope: "来自任务计划的工作范围。",
  knowledge: "建造过程中记录的笔记与检查结论。",
  history: "阶段历史和运行事件。",
  delivery: "来自 Host 的交付记录，不根据回复文字推断完成。",
};
export function BuildBranches({
  dock,
  branch,
  onBranch,
  summary,
  children,
}: {
  dock: HTMLElement | null;
  branch: BuildBranch | null;
  onBranch(branch: BuildBranch | null): void;
  summary: string;
  children: ReactNode;
}) {
  const rail = useRef<HTMLElement>(null);
  const capsule = useRef<HTMLSpanElement>(null);
  const reduced = useReducedMotion();
  const [position, setPosition] = useState({ left: 0, width: 0 });
  const [edges, setEdges] = useState({ left: false, right: true });
  const previous = useRef<BuildBranch | null>(null);
  useLayoutEffect(() => {
    const nav = rail.current;
    if (!nav || !dock) return;
    const measure = () => {
      const button = nav.querySelector<HTMLElement>('[aria-expanded="true"]');
      if (button)
        setPosition({ left: button.offsetLeft, width: button.offsetWidth });
      setEdges({
        left: nav.scrollLeft > 2,
        right: nav.scrollLeft + nav.clientWidth < nav.scrollWidth - 2,
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(nav);
    nav.addEventListener("scroll", measure, { passive: true });
    const button = nav.querySelector<HTMLElement>('[aria-expanded="true"]');
    if (button) {
      const target = Math.max(
        0,
        Math.min(
          nav.scrollWidth - nav.clientWidth,
          button.offsetLeft - (nav.clientWidth - button.offsetWidth) / 2,
        ),
      );
      nav.scrollTo({ left: target, behavior: reduced ? "instant" : "smooth" });
    }
    let animation: Animation | undefined;
    if (branch && previous.current !== branch && !reduced)
      animation = capsule.current?.animate(
        [
          { transform: "scale(1)" },
          { transform: "scale(1.12,.9)", offset: 0.32 },
          { transform: "scale(.97,1.025)", offset: 0.78 },
          { transform: "scale(1)" },
        ],
        { duration: 420, easing: "cubic-bezier(.2,.7,.2,1)" },
      );
    previous.current = branch;
    return () => {
      observer.disconnect();
      nav.removeEventListener("scroll", measure);
      animation?.cancel();
    };
  }, [branch, dock, reduced]);
  useLayoutEffect(() => {
    if (!branch) return;
    const back = (event: Event) => {
      if (
        document.querySelector(
          ".m-sheet-host:not([inert]), .user-message-image-lightbox:not([inert])",
        )
      )
        return;
      event.preventDefault();
      event.stopImmediatePropagation();
      onBranch(null);
    };
    window.addEventListener("loom:mobile-back", back, true);
    return () => window.removeEventListener("loom:mobile-back", back, true);
  }, [branch, onBranch]);
  return (
    <BuildBranchContext.Provider value={{ dock, branch }}>
      {dock
        ? createPortal(
            <div className="m-branches">
              <button
                type="button"
                className="m-build-summary"
                aria-label="查看建造状态与预算"
                onClick={() => onBranch(branch === "budget" ? null : "budget")}
              >
                {summary}
              </button>
              <div
                className="m-branch-track"
                data-left={edges.left || undefined}
                data-right={edges.right || undefined}
              >
                <nav
                  ref={rail}
                  className="m-branch-ports"
                  aria-label="建造支路"
                  onKeyDown={(event) => {
                    if (
                      !["ArrowLeft", "ArrowRight", "Home", "End"].includes(
                        event.key,
                      )
                    )
                      return;
                    const buttons = Array.from(
                      rail.current?.querySelectorAll<HTMLButtonElement>(
                        "button",
                      ) || [],
                    );
                    const at = buttons.indexOf(
                      document.activeElement as HTMLButtonElement,
                    );
                    const next =
                      event.key === "Home"
                        ? 0
                        : event.key === "End"
                          ? buttons.length - 1
                          : Math.max(
                              0,
                              Math.min(
                                buttons.length - 1,
                                at + (event.key === "ArrowRight" ? 1 : -1),
                              ),
                            );
                    event.preventDefault();
                    buttons[next]?.focus();
                  }}
                >
                  <i
                    className="m-branch-capsule"
                    aria-hidden="true"
                    style={{
                      width: position.width,
                      transform: `translateX(${position.left}px)`,
                      opacity: branch ? 1 : 0,
                    }}
                  >
                    <span ref={capsule} />
                  </i>
                  {entries.map(([key, label, Icon]) => (
                    <button
                      type="button"
                      key={key}
                      aria-expanded={branch === key}
                      aria-controls="mobile-build-branch"
                      onClick={() => onBranch(branch === key ? null : key)}
                    >
                      <Icon size={17} />
                      <span>{label}</span>
                    </button>
                  ))}
                </nav>
              </div>
              <span className="m-branch-scroll-hint" aria-hidden="true">
                {edges.right
                  ? "向左滑动查看更多"
                  : edges.left
                    ? "向右滑动返回常用支路"
                    : ""}
              </span>
            </div>,
            dock,
          )
        : null}
      <div
        id="mobile-build-branch"
        className="m-branch-content"
        data-branch={branch || "closed"}
      >
        {branch ? (
          <button
            type="button"
            className="m-inline-button m-branch-close"
            onClick={() => onBranch(null)}
          >
            <X size={16} />
            收起支路，返回对话
          </button>
        ) : null}
        {branch ? <p className="m-branch-intro">{notes[branch]}</p> : null}
        <div className="m-branch-body" key={branch || "closed"}>
          {children}
        </div>
      </div>
    </BuildBranchContext.Provider>
  );
}
