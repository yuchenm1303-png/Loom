import { useEffect, useMemo, useState } from "react";
import { Globe2, ArrowUpRight } from "./icons";
import { useI18n } from "../i18n";
import type { TranscriptItem } from "../types/loom";
import { browserImagePreview, collectBrowserActivities, type BrowserActivityTarget } from "./browserActivityModel";
import "./browser-activity-card.css";

export function BrowserActivityImage({ path, url, workspace, title }: { path: string; url: string; workspace?: string; title: string }) {
  const { language } = useI18n();
  const preview = browserImagePreview(url);
  const key = JSON.stringify([path, url, workspace]);
  const [image, setImage] = useState<{ key: string; source: string } | null>(null);
  const [failed, setFailed] = useState("");
  useEffect(() => {
    let alive = true;
    setFailed("");
    const load = async () => {
      const candidates = [...new Set([path, preview?.localPath].filter(Boolean))] as string[];
      if (workspace) for (const candidate of candidates) {
        try {
          const result = await window.loom.readLocalImage(candidate, workspace);
          if (alive) setImage({ key, source: result.dataUrl });
          return;
        } catch { /* Try the next local source, then the original image URL. */ }
      }
      if (!alive) return;
      if (preview) setImage({ key, source: preview.url });
      else if (path) setFailed(key);
    };
    void load();
    return () => { alive = false; };
  }, [key]);
  return image?.key === key && failed !== key
    ? <img src={image.source} alt={title} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(key)} />
    : <span className="browser-activity-placeholder"><Globe2 size={30} strokeWidth={1.2} aria-hidden="true" />
      <span>{language === "zh-CN" ? (failed === key ? "预览不可用 · 点击打开页面" : "点击在侧栏打开页面") : (failed === key ? "Preview unavailable · Open page" : "Open page in sidebar")}</span>
    </span>;
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
        <span className="browser-activity-visual"><BrowserActivityImage path={page.screenshot} url={page.url} workspace={workspace} title={title} />
          <span className="browser-activity-badge" data-state={page.status}><i />{labels[page.status]}</span>
        </span>
        <span className="browser-activity-footer"><span className="browser-activity-copy"><strong>{title}</strong>
          <span>{new URL(page.url).host} · {embedded ? (zh ? "内置浏览器" : "Loom browser") : (zh ? "外部浏览器" : "External browser")}</span>
        </span><span className="browser-activity-open">{zh ? "查看" : "Open"}<ArrowUpRight size={14} /></span></span>
      </button>;
    })}
  </div>;
}
