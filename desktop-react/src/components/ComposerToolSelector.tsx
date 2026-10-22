import { useEffect, useRef, useState } from "react";
import { RefreshCw, Search, SlidersHorizontal } from "./icons";
import { useI18n } from "../i18n";
import { ComposerPanel, PanelFoot, PanelHead, Segmented, useComposerPanel } from "./composerPanel";

interface Tool { name: string; description: string; available: boolean; deferred?: boolean }
interface Catalog { tools: Tool[]; skills: { name: string; description: string; short_description?: string }[]; errors?: string[] }
const groups = ["web", "files", "execution", "browser", "agents", "skills", "other", "mcp"] as const;
type Group = typeof groups[number];
type Mode = "auto" | "custom" | "off";
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

/**
 * Which tools the next turn may use: Auto (the model chooses among every available tool), Custom (only the ticked
 * ones) or Off (chat only). Ticking or unticking a tool in Auto or Off makes the set custom. Skills are inserted into
 * the draft as a reference. One of the composer's panels (composerPanel.tsx).
 */
export function ComposerToolSelector({ threadId, workspace, selection, onChange, disabled, readOnly = false, onInsertSkill }: {
  threadId?: string; workspace?: string; selection: string[] | null; onChange(value: string[] | null): void;
  disabled?: boolean; readOnly?: boolean; onInsertSkill(name: string): void;
}) {
  const zh = useI18n().language === "zh-CN";
  const panel = useComposerPanel("tools");
  const { close } = panel;
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<"tools" | "skills">("tools");
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => { close(); setCatalog(null); setQuery(""); setError(""); }, [threadId, workspace, close]);
  useEffect(() => { if (disabled) close(); }, [disabled, close]);
  useEffect(() => {
    if (!panel.open) return;
    let alive = true;
    setBusy(true); setError("");
    window.loom.call<Catalog>("tools/catalog", threadId ? { threadId } : { workspace }).then(result => {
      if (!Array.isArray(result.tools) || !Array.isArray(result.skills)) throw new Error(zh ? "工具列表格式不正确，请更新 Host。" : "Invalid tool catalog. Update the Host.");
      if (alive) setCatalog(result);
    }).catch(cause => { if (alive) setError(cause instanceof Error ? cause.message : String(cause)); })
      .finally(() => { if (alive) setBusy(false); });
    search.current?.focus();
    return () => { alive = false; };
  }, [panel.open, threadId, workspace, revision, zh]);
  const tools = catalog?.tools ?? [];
  const available = tools.filter(tool => tool.available);
  const selected = new Set(selection ?? available.map(tool => tool.name));
  const title = zh ? "工具与技能" : "Tools & skills";
  const count = selection?.length;
  const mode: Mode = selection === null ? "auto" : selection.length === 0 ? "off" : "custom";
  // A refresh keeps the list it had: only a panel with no catalog yet (or a failed one) is waiting.
  const unready = !catalog || Boolean(error);
  const locked = readOnly || unready;
  const filtered = (value: string) => value.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase());
  const toggle = (entries: Tool[], checked: boolean) => {
    const next = new Set(selected);
    for (const tool of entries) if (tool.available) { if (checked) next.add(tool.name); else next.delete(tool.name); }
    onChange([...next].sort());
  };
  const chooseMode = (next: Mode) => {
    if (next === mode) return;
    if (next === "auto") onChange(null);
    else if (next === "off") onChange([]);
    else onChange(available.map(tool => tool.name).sort());
  };
  const visibleTools = tools.filter(tool => filtered(`${tool.name} ${tool.description} ${names[tool.name] || ""}`));
  const skills = (catalog?.skills ?? []).filter(skill => filtered(`${skill.name} ${skill.description}`));
  const help = mode === "auto"
    ? (zh ? "由模型按需使用可用工具。" : "Let the model use available tools as needed.")
    : mode === "off" ? (zh ? "纯对话模式：下一轮不调用工具。" : "Chat only: no tools in the next turn.")
    : (zh ? `下一轮仅提供已选的 ${count} 项工具。` : `Only ${count} selected tools are available next turn.`);
  const rule = readOnly
    ? (zh ? "任务执行中，可查看工具；任务结束后可修改。" : "View tools while the task runs. Edit after it finishes.")
    : (zh ? "发送时生效，仍遵循权限与审批规则。" : "Applied when you send. Permissions and approvals still apply.");
  return <div ref={panel.anchorRef} className="composer-tool-selector cp-anchor">
    <button ref={panel.triggerRef} type="button"
      className={`composer-tool composer-icon-action composer-tools-trigger ${panel.open ? "is-open" : ""} ${count !== undefined ? "has-count" : ""}`}
      aria-label={title} title={`${title} · ${count === undefined ? (zh ? "自动" : "Auto") : `${count} ${zh ? "项已选" : "selected"}`}`}
      aria-expanded={panel.open} aria-haspopup="dialog" disabled={disabled} onClick={panel.toggle}>
      <SlidersHorizontal size={14} />{count !== undefined ? <span className="composer-tools-count">{count}</span> : null}
    </button>
    <ComposerPanel panel={panel} label={title} size="l" className="composer-tools-popover">
      <PanelHead title={title} aside={<button type="button" className="cp-icon composer-tools-refresh" title={zh ? "刷新工具与技能" : "Refresh tools & skills"}
        aria-label={zh ? "刷新工具与技能" : "Refresh tools & skills"} disabled={busy} onClick={() => setRevision(value => value + 1)}>
        <RefreshCw size={14} spinning={busy} /></button>} />
      <div className="cp-tools-bar">
        <Segmented<Mode> className="composer-tools-modes" role="radiogroup" label={zh ? "工具模式" : "Tool mode"} value={mode} onChange={chooseMode}
          options={[
            { value: "auto", label: zh ? "自动" : "Auto", disabled: readOnly },
            { value: "custom", label: zh ? "自选" : "Custom", disabled: locked },
            { value: "off", label: zh ? "关闭" : "Off", disabled: readOnly },
          ]} />
        <div className="cp-tools-filter">
          <label className="cp-search composer-tools-search"><Search size={14} strokeWidth={1.75} aria-hidden="true" /><input ref={search} value={query}
            onChange={event => setQuery(event.target.value)} onKeyDown={event => {
              if (event.key === "Escape" && query) { event.preventDefault(); event.stopPropagation(); setQuery(""); }
            }}
            aria-label={zh ? "搜索工具与技能" : "Search tools & skills"} placeholder={zh ? "搜索…" : "Search…"} autoComplete="off" spellCheck={false} /></label>
          <Segmented<"tools" | "skills"> className="composer-tools-tabs" label={title} value={tab} onChange={setTab} options={[
            { value: "tools", label: <>{zh ? "工具" : "Tools"}<small>{tools.length}</small></> },
            { value: "skills", label: <>{zh ? "技能" : "Skills"}<small>{catalog?.skills.length ?? 0}</small></> },
          ]} />
        </div>
      </div>
      <div className="cp-body composer-tools-list" data-mode={mode}>
        <div key={tab} className="cp-view">
          {busy && !catalog ? <div className="cp-empty" role="status">{zh ? "正在获取工具列表…" : "Loading tools…"}</div> : null}
          {error ? <div className="cp-note is-error" role="alert"><span>{error}<br />{zh ? "请确认 Host 已更新后重试。" : "Update the Host and retry."}</span></div> : null}
          {tab === "tools" ? groups.map(group => {
            const entries = visibleTools.filter(tool => groupOf(tool.name) === group);
            if (!entries.length) return null;
            const all = tools.filter(tool => groupOf(tool.name) === group && tool.available);
            const checked = all.length > 0 && all.every(tool => selected.has(tool.name));
            const partial = !checked && all.some(tool => selected.has(tool.name));
            const label = labels[group][zh ? 0 : 1];
            return <section key={group} aria-label={label}>
              <label className="cp-label cp-group-label composer-tools-group-title">
                <input type="checkbox" className="cp-check" aria-label={`${zh ? "选取整组" : "Select group"} · ${label}`} checked={checked}
                  ref={element => { if (element) element.indeterminate = partial; }} disabled={readOnly || !all.length || unready}
                  onChange={event => toggle(all, event.target.checked)} />
                <span>{label}</span>
              </label>
              {entries.map(tool => <label className="cp-row is-tall cp-tool composer-tools-row" key={tool.name} data-unavailable={!tool.available} title={tool.description}>
                <input type="checkbox" className="cp-check" data-cp-item="" checked={tool.available && selected.has(tool.name)}
                  disabled={readOnly || !tool.available || unready} onChange={event => toggle([tool], event.target.checked)} />
                <span className="cp-row-text">
                  <span className="cp-row-title"><span>{zh ? names[tool.name] || tool.name : tool.name}</span>
                    {!tool.available ? <span className="cp-tag">{zh ? "设置中已关闭" : "Off in settings"}</span> : null}</span>
                  <span className="cp-row-desc">{tool.description}</span>
                </span>
              </label>)}
            </section>;
          }) : skills.map(skill => <button className="cp-row is-tall cp-skill composer-tools-skill" type="button" key={skill.name} data-cp-item=""
            disabled={readOnly || unready} onClick={() => {
              if (selection !== null) onChange([...new Set([...selection, ...available.filter(tool => tool.name.startsWith("skill_")).map(tool => tool.name)])]);
              onInsertSkill(skill.name); panel.close(true);
            }}>
            <span className="cp-row-text">
              <span className="cp-row-title"><span>{skill.name}</span></span>
              <span className="cp-row-desc">{skill.short_description || skill.description}</span>
            </span>
            <span className="cp-row-meta">{zh ? "插入引用" : "Insert"}</span>
          </button>)}
          {!busy && !error && !(tab === "tools" ? visibleTools.length : skills.length) ? <div className="cp-empty">{zh ? "没有匹配的项目" : "No matching items"}</div> : null}
          {catalog?.errors?.length ? <div className="cp-note"><span>{catalog.errors.join("\n")}</span></div> : null}
        </div>
      </div>
      <PanelFoot><span>{zh ? help + rule : `${help} ${rule}`}</span></PanelFoot>
    </ComposerPanel>
  </div>;
}
