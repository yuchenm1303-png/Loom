import { useEffect, useMemo, useState } from "react";
import { Globe2, ArrowUpRight } from "./icons";
import { useI18n } from "../i18n";
import type { TranscriptItem } from "../types/loom";
import { collectBrowserActivities, type BrowserActivityTarget } from "./browserActivityModel";
import "./browser-activity-card.css";

export function BrowserActivityImage({ path, workspace, title }: { path: string; workspace?: string; title: string }) {
  const [image, setImage] = useState<{ path: string; workspace?: string; source: string } | null>(null);
  useEffect(() => {
    let alive = true;
    if (path && workspace) void window.loom.readLocalImage(path, workspace).then(result => {
      if (alive) setImage({ path, workspace, source: result.dataUrl });
    }).catch(() => {});
    return () => { alive = false; };
  }, [path, workspace]);
  return image?.path === path && image.workspace === workspace
    ? <img src={image.source} alt={title} loading="lazy" decoding="async" />
    : <span className="browser-activity-placeholder" aria-hidden="true"><Globe2 size={30} strokeWidth={1.2} /></span>;
}

export function BrowserActivityCards({ items, active, workspace }: { items: TranscriptItem[]; active: boolean; workspace?: string }) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const pages = useMemo(() => collectBrowserActivities(items, active), [items, active]);
  if (!pages.length) return null;
  const labels = zh
    ? { working: "正在操作", waiting: "等待确认", failed: "操作未完成", closed: "会话已关闭", ready: "最近浏览" }
    : { working: "Working", waiting: "Needs approval", failed: "Action incomplete", closed: "Session closed", ready: "Recently browsed" };
  return <div className="browser-activity-cards" aria-label={zh ? "浏览器页面" : "Browser pages"}>
    {pages.map(page => {
      const embedded = page.connection === "embedded";
      const title = page.title || new URL(page.url).hostname;
      const target: BrowserActivityTarget = { ...page, workspace };
      return <button type="button" className="browser-activity-card" key={page.id}
        disabled={typeof window.loom.embeddedBrowser !== "function"}
        aria-label={`${zh ? "在侧栏查看" : "View in sidebar"} · ${title}`}
        onClick={() => window.dispatchEvent(new CustomEvent("loom:browser-activity-open", { detail: target }))}>
        <span className="browser-activity-visual"><BrowserActivityImage path={page.screenshot} workspace={workspace} title={title} />
          <span className="browser-activity-badge" data-state={page.status}><i />{labels[page.status]}</span>
        </span>
        <span className="browser-activity-footer"><span className="browser-activity-copy"><strong>{title}</strong>
          <span>{new URL(page.url).host} · {embedded ? (zh ? "内置浏览器" : "Loom browser") : (zh ? "外部浏览器" : "External browser")}</span>
        </span><span className="browser-activity-open">{zh ? "查看" : "Open"}<ArrowUpRight size={14} /></span></span>
      </button>;
    })}
  </div>;
}
