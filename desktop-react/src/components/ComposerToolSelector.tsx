import { useEffect, useRef, useState } from "react";
import { FolderOpen, Globe, RefreshCw, Search, SlidersHorizontal, Wrench, X } from "./icons";
import { useI18n } from "../i18n";
import "./composer-tool-selector.css";

interface Tool { name: string; description: string; available: boolean; deferred?: boolean }
interface Catalog { tools: Tool[]; skills: { name: string; description: string; short_description?: string }[]; errors?: string[] }
const groups = ["web", "files", "execution", "browser", "agents", "skills", "other", "mcp"] as const;
type Group = typeof groups[number];
function groupOf(name: string): Group {
  if (name.startsWith("mcp.")) return "mcp";
  if (/^(web_|fetch|search_web)/.test(name)) return "web";
  if (/^(browser_|computer_|ufo_)/.test(name)) return "browser";
  if (/^(skill_)/.test(name)) return "skills";
  if (/^(agent_|subagent_|spawn_|wait_agent|send_message)/.test(name)) return "agents";
  if (/file|workspace|directory|glob|ripgrep|patch|^rg$/.test(name)) return "files";
  if (/shell|command|process|terminal|code_mode|exec/.test(name)) return "execution";
  return "other";
}
const labels: Record<Group, [string, string]> = {
  web: ["网络与搜索", "Web & search"], files: ["项目与文件", "Project & files"], execution: ["命令与进程", "Commands & processes"],
  browser: ["浏览器与电脑", "Browser & computer"], agents: ["代理协作", "Agents"], skills: ["技能工具", "Skill tools"],
  other: ["其他工具", "Other tools"], mcp: ["MCP 服务器工具", "MCP server tools"],
};
const names: Record<string, string> = {
  web_fetch: "网络获取", web_search: "网络搜索", glob: "查找文件", glob_files: "查找文件", read_file: "读取文件", write_file: "写入文件",
  apply_patch: "补丁编辑", ripgrep: "全文搜索", shell: "执行命令", tool_search: "发现工具", skill_load: "加载技能", skill_search: "搜索技能",
  read_workspace_text: "读取文件", search_workspace_text: "全文搜索", write_workspace_text: "写入文件", replace_workspace_text: "替换文件内容",
  write_workspace_note: "写入工作区笔记", append_workspace_jsonl: "追加结构化记录", exec_command: "执行命令", write_stdin: "进程输入",
  browser_open: "打开浏览器", browser_state: "读取页面", browser_click: "点击页面", browser_type: "输入页面内容", browser_screenshot: "浏览器截图",
  browser_navigate: "访问网页", browser_status: "浏览器状态", web_search_status: "搜索服务状态", skill_read_resource: "读取技能资源",
};
export function ComposerToolSelector({ threadId, workspace, selection, onChange, disabled, onOpen, onInsertSkill }: {
  threadId?: string; workspace?: string; selection: string[] | null; onChange(value: string[] | null): void;
  disabled?: boolean; onOpen(): void; onInsertSkill(name: string): void;
}) {
  const zh = useI18n().language === "zh-CN";
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"tools" | "skills">("tools");
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => { setOpen(false); setCatalog(null); setQuery(""); setError(""); }, [threadId, workspace]);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  useEffect(() => {
    if (!open) return;
    let alive = true;
    setBusy(true); setError("");
    window.loom.call<Catalog>("tools/catalog", threadId ? { threadId } : { workspace }).then(result => {
      if (!Array.isArray(result.tools) || !Array.isArray(result.skills)) throw new Error(zh ? "工具列表格式不正确，请更新 Host。" : "Invalid tool catalog. Update the Host.");
      if (alive) setCatalog(result);
    }).catch(cause => { if (alive) setError(cause instanceof Error ? cause.message : String(cause)); })
      .finally(() => { if (alive) setBusy(false); });
    search.current?.focus();
    return () => { alive = false; };
  }, [open, threadId, workspace, revision, zh]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  const tools = catalog?.tools ?? [];
  const available = tools.filter(tool => tool.available);
  const selected = new Set(selection ?? available.map(tool => tool.name));
  const title = zh ? "工具与技能" : "Tools & skills";
  const count = selection?.length;
  const filtered = (value: string) => value.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
  const toggle = (entries: Tool[], checked: boolean) => {
    const next = new Set(selected);
    for (const tool of entries) if (tool.available) { if (checked) next.add(tool.name); else next.delete(tool.name); }
    onChange([...next].sort());
  };
  const visibleTools = tools.filter(tool => filtered(`${tool.name} ${tool.description} ${names[tool.name] || ""}`));
  const skills = (catalog?.skills ?? []).filter(skill => filtered(`${skill.name} ${skill.description}`));
  return <div ref={root} className="composer-tool-selector">
    <button ref={trigger} type="button" className={`composer-tool composer-icon-action composer-tools-trigger ${open ? "is-open" : ""}`}
      aria-label={title} title={`${title} · ${count === undefined ? (zh ? "自动" : "Auto") : `${count} ${zh ? "项已选" : "selected"}`}`}
      aria-expanded={open} aria-haspopup="dialog" disabled={disabled} onClick={() => { if (!open) onOpen(); setOpen(value => !value); }}>
      <SlidersHorizontal size={14} />{count !== undefined ? <span className="composer-tools-count">{count}</span> : null}
    </button>
    {open ? <section className="composer-popover composer-tools-popover" role="dialog" aria-label={title}>
      <header className="composer-tools-head"><strong>{title}</strong><button type="button" className="composer-tool composer-icon-action"
        aria-label={zh ? "关闭工具选择" : "Close tool selection"} onClick={() => { setOpen(false); trigger.current?.focus(); }}><X size={14} /></button></header>
      <div className="composer-tools-modes">
        <button type="button" aria-pressed={selection === null} onClick={() => onChange(null)}>{zh ? "自动" : "Auto"}</button>
        <button type="button" disabled={!catalog || busy || Boolean(error)} onClick={() => onChange(available.map(tool => tool.name))}>{zh ? "全选" : "Select all"}</button>
        <button type="button" disabled={!catalog || busy || Boolean(error)} aria-pressed={selection?.length === 0} onClick={() => onChange([])}>{zh ? "全不选" : "Select none"}</button>
        <button type="button" className="composer-tools-refresh" title={zh ? "刷新工具与技能" : "Refresh tools & skills"}
          aria-label={zh ? "刷新工具与技能" : "Refresh tools & skills"} disabled={busy} onClick={() => setRevision(value => value + 1)}><RefreshCw size={13} /></button>
      </div>
      <p className="composer-tools-help">{selection === null
        ? (zh ? "由模型按需使用可用工具。" : "Let the model use available tools as needed.")
        : selection.length === 0 ? (zh ? "纯对话模式：下一轮不调用工具。" : "Chat only: no tools in the next turn.")
        : (zh ? `下一轮仅提供已选的 ${selection.length} 项工具。` : `Only ${selection.length} selected tools are available next turn.`)}</p>
      <label className="composer-tools-search"><Search size={13} /><input ref={search} value={query} onChange={event => setQuery(event.target.value)}
        aria-label={zh ? "搜索工具与技能" : "Search tools & skills"} placeholder={zh ? "搜索工具与技能…" : "Search tools & skills…"} /></label>
      <div className="composer-tools-tabs" role="tablist" aria-label={title}>
        {(["tools", "skills"] as const).map(id => <button key={id} type="button" role="tab" aria-selected={tab === id}
          onClick={() => setTab(id)}>{id === "tools" ? (zh ? "工具" : "Tools") : (zh ? "技能" : "Skills")}
          <small>{id === "tools" ? tools.length : catalog?.skills.length ?? 0}</small></button>)}
      </div>
      <div className="composer-tools-list">
        {busy ? <p role="status">{zh ? "正在获取工具列表…" : "Loading tools…"}</p> : null}
        {error ? <p role="alert">{error}<br />{zh ? "请确认 Host 已更新后重试。" : "Update the Host and retry."}</p> : null}
        {tab === "tools" ? groups.map(group => {
          const entries = visibleTools.filter(tool => groupOf(tool.name) === group);
          if (!entries.length) return null;
          const all = tools.filter(tool => groupOf(tool.name) === group && tool.available);
          const checked = all.length > 0 && all.every(tool => selected.has(tool.name));
          const partial = !checked && all.some(tool => selected.has(tool.name));
          const Icon = group === "web" ? Globe : group === "files" ? FolderOpen : Wrench;
          return <div className="composer-tools-group" key={group}>
            <label className="composer-tools-group-title"><Icon size={13} /><span>{labels[group][zh ? 0 : 1]}</span>
              <input type="checkbox" aria-label={`${zh ? "选取整组" : "Select group"} · ${labels[group][zh ? 0 : 1]}`} checked={checked}
                ref={element => { if (element) element.indeterminate = partial; }} disabled={!all.length || busy || Boolean(error)} onChange={event => toggle(all, event.target.checked)} /></label>
            {entries.map(tool => <label className="composer-tools-row" key={tool.name} data-unavailable={!tool.available}>
              <input type="checkbox" checked={tool.available && selected.has(tool.name)} disabled={!tool.available || busy || Boolean(error)}
                onChange={event => toggle([tool], event.target.checked)} />
              <span><strong>{zh ? names[tool.name] || tool.name : tool.name}</strong><small>{tool.description}</small>
                {!tool.available ? <em>{zh ? "当前设置已禁用" : "Disabled in settings"}</em> : null}</span></label>)}
          </div>;
        }) : skills.map(skill => <button className="composer-tools-skill" type="button" key={skill.name} disabled={busy}
          onClick={() => {
            if (selection !== null) onChange([...new Set([...selection, ...available.filter(tool => tool.name.startsWith("skill_")).map(tool => tool.name)])]);
            onInsertSkill(skill.name); setOpen(false);
          }}><strong>{skill.name}</strong><small>{skill.short_description || skill.description}</small><em>{zh ? "插入技能引用" : "Insert skill reference"}</em></button>)}
        {!busy && !error && !(tab === "tools" ? visibleTools.length : skills.length) ? <p>{zh ? "没有匹配的项目" : "No matching items"}</p> : null}
        {catalog?.errors?.length ? <p className="composer-tools-warning">{catalog.errors.join("\n")}</p> : null}
      </div>
      <footer>{zh ? "选择在发送时生效，仍遵循权限与审批规则。" : "Applied when you send. Permissions and approvals still apply."}</footer>
    </section> : null}
  </div>;
}
