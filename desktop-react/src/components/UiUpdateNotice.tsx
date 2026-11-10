import { useEffect, useState } from "react";
import { useI18n } from "../i18n";
import "./ui-update-notice.css";

type Status = { phase: "waiting" | "updated"; build: number };
export function UiUpdateNotice() {
  const { language } = useI18n();
  const zh = language.startsWith("zh");
  const [status, setStatus] = useState<Status | null>(null);
  const [dismissed, setDismissed] = useState("");
  useEffect(() => {
    let mounted = true;
    const receive = (value: Status | null) => { if (mounted) setStatus(value); };
    const unsubscribe = window.loom?.onUiUpdateStatus?.(receive);
    void window.loom?.getUiUpdateStatus?.().then(receive).catch(() => {});
    return () => { mounted = false; unsubscribe?.(); };
  }, []);
  const key = status ? `${status.build}:${status.phase}` : "";
  if (!status || dismissed === key) return null;
  return <aside className="ui-update-notice" role="status" aria-live="polite">
    <div><strong>{status.phase === "updated" ? (zh ? "界面已更新" : "Interface updated") : (zh ? "界面更新已就绪" : "Interface update ready")}</strong>
      <p>{status.phase === "updated"
        ? (zh ? "已自动更新到新版界面，后台任务继续运行。" : "The new interface is ready. Background tasks keep running.")
        : (zh ? "有未保存内容或正在操作，完成后将自动更新。" : "The interface will update automatically after you finish editing.")}</p>
    </div>
    <button type="button" onClick={() => setDismissed(key)} aria-label={zh ? "关闭更新提示" : "Dismiss update notice"}>×</button>
  </aside>;
}
