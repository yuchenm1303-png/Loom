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
  ListChecks,
  Bot,
  Gauge,
  X,
  History,
  RotateCw,
} from "../components/icons";
import { useReducedMotion } from "../motion/useReducedMotion";

/** Five branches, all on the rail at once. Everything a build can show has one home among them:
 *  run = state, budget, model and quota; tasks = tasks, guidance, scope; project = folder and references;
 *  records = delivery, knowledge, history. */
export type BuildBranch = "budget" | "tasks" | "agents" | "project" | "records";

export const BuildBranchContext = createContext<{
  branch: BuildBranch | null;
  dock: HTMLElement | null;
}>({ branch: null, dock: null });

/** Shown only on its branch; urgent content rises into the dock above the rail on every branch. */
export function BranchCard({
  urgent,
  category = "budget",
  children,
}: {
  urgent?: boolean;
  category?: BuildBranch;
  children: ReactNode;
}) {
  const { branch, dock } = useContext(BuildBranchContext);
  if (urgent && dock) return createPortal(children, dock);
  return branch === category ? children : null;
}

/** One block of a branch page: a small label over its content. The page header names the branch, so nothing
 *  inside a page repeats it as a large title. */
export function BranchGroup({
  label,
  aside,
  children,
}: {
  label: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="m-group">
      <div className="m-group-head">
        <h2>{label}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function BranchEmpty({
  category,
  children,
}: {
  category: BuildBranch;
  children: ReactNode;
}) {
  return (
    <BranchCard category={category}>
      <p className="m-branch-empty">{children}</p>
    </BranchCard>
  );
}

const entries = [
  ["budget", "运行", Gauge],
  ["tasks", "任务", ListChecks],
  ["agents", "Agent", Bot],
  ["project", "项目", FolderOpen],
  ["records", "记录", History],
] as const;

export function BuildBranches({
  dock,
  branch,
  onBranch,
  summary,
  refresh,
  children,
}: {
  dock: HTMLElement | null;
  branch: BuildBranch | null;
  onBranch(branch: BuildBranch | null): void;
  summary: string;
  refresh?: { busy: boolean; disabled: boolean; run(): void };
  children: ReactNode;
}) {
  const rail = useRef<HTMLElement>(null);
  const capsule = useRef<HTMLSpanElement>(null);
  const reduced = useReducedMotion();
  const [position, setPosition] = useState({ left: 0, width: 0 });
  const previous = useRef<BuildBranch | null>(null);
  useLayoutEffect(() => {
    const nav = rail.current;
    if (!nav || !dock) return;
    const measure = () => {
      const button = nav.querySelector<HTMLElement>('[aria-expanded="true"]');
      if (button)
        setPosition({ left: button.offsetLeft, width: button.offsetWidth });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(nav);
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
  const title = entries.find(([key]) => key === branch)?.[1];
  return (
    <BuildBranchContext.Provider value={{ dock, branch }}>
      {dock
        ? createPortal(
            <div className="m-branches">
              <p className="m-build-summary">{summary}</p>
              <div className="m-branch-track">
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
                      <Icon size={16} />
                      <span>{label}</span>
                    </button>
                  ))}
                </nav>
              </div>
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
          <header className="m-branch-head">
            <h2>{title}</h2>
            {refresh ? (
              <button
                type="button"
                className="m-icon-button"
                aria-label="刷新建造状态"
                disabled={refresh.disabled}
                onClick={refresh.run}
              >
                <RotateCw size={17} strokeWidth={1.75} spinning={refresh.busy} />
              </button>
            ) : null}
            <button
              type="button"
              className="m-icon-button"
              aria-label="收起支路，返回对话"
              onClick={() => onBranch(null)}
            >
              <X size={18} strokeWidth={1.75} />
            </button>
          </header>
        ) : null}
        <div className="m-branch-body" key={branch || "closed"}>
          {children}
        </div>
      </div>
    </BuildBranchContext.Provider>
  );
}
