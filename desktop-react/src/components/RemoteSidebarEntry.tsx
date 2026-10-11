import { MonitorSmartphone } from "./icons";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useI18n } from "../i18n";
import { webExecutionMode } from "../webBridge";

export function RemoteSidebarEntry() {
  const { language } = useI18n();
  const label = language === "zh-CN" ? "远程控制" : "Remote";
  const [target, setTarget] = useState<HTMLElement | null>(null);
  const remote = webExecutionMode() === "remote";

  useEffect(() => {
    const syncTarget = () => {
      const next = document.querySelector<HTMLElement>(".codex-sidebar-primary");
      setTarget((current) => current === next ? current : next);
    };
    syncTarget();
    const observer = new MutationObserver(syncTarget);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  if (!target) return null;
  return createPortal(
    <button
      type="button"
      className={`sidebar-secondary-action remote-sidebar-entry ${remote ? "is-active" : ""}`.trim()}
      onClick={() => window.dispatchEvent(new CustomEvent("loom:web-open-remote"))}
      aria-current={remote ? "page" : undefined}
    >
      <MonitorSmartphone size={16} strokeWidth={1.75} />
      <span>{label}</span>
      {remote ? <span className="remote-sidebar-live-dot" aria-hidden="true" /> : null}
    </button>,
    target,
  );
}
