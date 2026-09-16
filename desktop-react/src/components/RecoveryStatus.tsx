import { CircleStop, Play, RefreshCw, ShieldCheck, WifiOff } from "lucide-react";
import { useI18n } from "../i18n";
import "./recovery-status.css";

export type RecoveryDisplayState =
  | "idle"
  | "reconnecting"
  | "recovering"
  | "safely_interrupted"
  | "cancelled";

interface RecoveryBannerProps {
  state: RecoveryDisplayState;
  onContinue?(): Promise<void> | void;
}

interface RecoveryCopy {
  title: string;
  detail: string;
  action?: string;
}

function copyFor(state: RecoveryDisplayState, zh: boolean): RecoveryCopy {
  if (state === "reconnecting") {
    return zh
      ? {
          title: "连接暂时中断，正在检查恢复条件…",
          detail: "任务进度已经保存。Loom 会先确认可以安全继续，再恢复执行。",
        }
      : {
          title: "Connection interrupted. Checking recovery…",
          detail: "Your progress is saved. Loom will verify a safe recovery point before continuing.",
        };
  }
  if (state === "recovering") {
    return zh
      ? {
          title: "正在恢复上次任务…",
          detail: "Loom 正从最后一个已确认的持久化状态继续，不会重放结果未知的操作。",
        }
      : {
          title: "Recovering the previous task…",
          detail: "Loom is continuing from the last confirmed durable state without replaying actions with unknown outcomes.",
        };
  }
  if (state === "safely_interrupted") {
    return zh
      ? {
          title: "任务已安全暂停",
          detail: "中断发生在无法确认操作结果的位置。为避免重复产生副作用，Loom 没有自动重放这一步。",
          action: "安全继续",
        }
      : {
          title: "Task paused safely",
          detail: "The interruption happened where an action result could not be confirmed. Loom did not replay that step to avoid duplicate side effects.",
          action: "Continue safely",
        };
  }
  if (state === "cancelled") {
    return zh
      ? {
          title: "任务已停止",
          detail: "这是一次用户停止。Loom 不会自动恢复这个任务；你可以直接发送新的指令。",
        }
      : {
          title: "Task stopped",
          detail: "This was a user stop. Loom will not automatically resume this task; you can send a new instruction whenever you want.",
        };
  }
  return { title: "", detail: "" };
}

function StateIcon({ state }: { state: RecoveryDisplayState }) {
  if (state === "reconnecting") return <WifiOff size={15} strokeWidth={1.9} />;
  if (state === "recovering") return <RefreshCw size={15} strokeWidth={1.9} />;
  if (state === "safely_interrupted") return <ShieldCheck size={15} strokeWidth={1.9} />;
  return <CircleStop size={15} strokeWidth={1.9} />;
}

export function RecoveryBanner({ state, onContinue }: RecoveryBannerProps) {
  const { language } = useI18n();
  if (state === "idle") return null;

  const copy = copyFor(state, language === "zh-CN");
  const active = state === "reconnecting" || state === "recovering";

  return (
    <div className={`recovery-banner-frame recovery-${state}`} role="status" aria-live="polite">
      <div className="recovery-banner-icon" aria-hidden="true"><StateIcon state={state} /></div>
      <div className="recovery-banner-copy">
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
      {state === "safely_interrupted" && copy.action && onContinue ? (
        <button type="button" className="recovery-continue" onClick={() => void onContinue()}>
          <Play size={13} fill="currentColor" />
          <span>{copy.action}</span>
        </button>
      ) : active ? (
        <span className="recovery-live" aria-hidden="true"><i /><i /><i /></span>
      ) : null}
    </div>
  );
}

export function RecoveryComposer({ state }: { state: "reconnecting" | "recovering" }) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const recovering = state === "recovering";

  return (
    <div className={`recovery-composer recovery-${state}`} role="status" aria-live="polite">
      <span className="recovery-composer-icon" aria-hidden="true">
        {recovering ? <RefreshCw size={16} /> : <WifiOff size={16} />}
      </span>
      <div>
        <strong>{recovering ? (zh ? "正在恢复任务…" : "Recovering task…") : (zh ? "正在重新连接…" : "Reconnecting…")}</strong>
        <span>{recovering
          ? (zh ? "已保存的进度保持不变" : "Saved progress is preserved")
          : (zh ? "不会重新执行已经确认完成的操作" : "Confirmed actions will not be repeated")}</span>
      </div>
      <span className="recovery-composer-spinner" aria-hidden="true" />
    </div>
  );
}
