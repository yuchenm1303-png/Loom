import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";
import "./settings-mcp.css";

type Server = { name: string; transport: string; command: string; args: string[]; cwd: string; url: string; env_from: Record<string, string>; bearer_token_env: string; enabled: boolean; timeout_seconds: number; editable?: boolean; connected?: boolean; tool_count?: number; error?: string };
type Snapshot = { servers: Server[]; connected_servers: number; remote_tool_count: number; sdk_available: boolean; config_path: string; tools: { server: string; name: string; description: string }[] };
const blank = (): Server => ({ name: "", transport: "http", command: "", args: [], cwd: "", url: "", env_from: {}, bearer_token_env: "", enabled: true, timeout_seconds: 30 });
const presets = [
  { name: "GitHub", description: ["Repositories, issues and pull requests", "仓库、Issue 和 Pull Request"], docs: "https://github.com/github/github-mcp-server", server: { name: "github", url: "https://api.githubcopilot.com/mcp/", bearer_token_env: "GITHUB_TOKEN" } },
  { name: "Context7", description: ["Library documentation and examples", "编程库文档与示例"], docs: "https://github.com/upstash/context7", server: { name: "context7", url: "https://mcp.context7.com/mcp", bearer_token_env: "CONTEXT7_API_KEY" } },
  { name: "Fetch", description: ["Read web pages · requires uvx", "读取网页 · 需要安装 uvx"], docs: "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch", server: { name: "fetch", transport: "stdio", command: "uvx", args: ["mcp-server-fetch"] } },
];

export function McpSettingsPanel({ running, enabled }: { running: boolean; enabled: boolean }) {
  const { language } = useI18n();
  const zh = language.startsWith("zh");
  const t = (en: string, cn: string) => zh ? cn : en;
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Server | null>(null);
  const [editing, setEditing] = useState("");
  const [args, setArgs] = useState("[]");
  const [env, setEnv] = useState("{}");
  const [token, setToken] = useState("");
  const [search, setSearch] = useState("");
  const [deleting, setDeleting] = useState("");
  const formRef = useRef<HTMLFormElement>(null);
  const locked = running || busy;
  const formOpen = draft !== null;
  useEffect(() => { if (formOpen) formRef.current?.scrollIntoView({ block: "start" }); }, [formOpen, editing]);
  async function refresh() {
    setBusy(true); setError("");
    try { setSnapshot(await window.loom.call<Snapshot>("mcp/list", {})); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  useEffect(() => { void refresh(); }, []);
  function open(server: Server, name = "") {
    setToken(""); setDraft({ ...server }); setEditing(name); setArgs(JSON.stringify(server.args, null, 2)); setEnv(JSON.stringify(server.env_from, null, 2)); setError(""); setNotice(""); setDeleting("");
  }
  async function manage(action: string, name: string, server?: Server) {
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await window.loom.call<Snapshot>("mcp/manage", { action, name, server, ...(action === "save" && token ? { token } : {}) });
      setSnapshot(result); setToken(""); setDraft(null); setDeleting("");
      const changed = result.servers.find(s => s.name === (server?.name ?? name));
      if (changed?.enabled && !changed.connected) setError(changed.error || t("Saved, but not connected. Check the configuration and retry.", "配置已保存，但尚未连接，请检查配置后重试。"));
      else setNotice(t("MCP configuration applied.", "MCP 配置已生效。"));
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  function save(event: React.FormEvent) {
    event.preventDefault();
    if (!draft) return;
    try {
      const parsedArgs: unknown = JSON.parse(args), parsedEnv: unknown = JSON.parse(env);
      if (!Array.isArray(parsedArgs) || !parsedArgs.every(x => typeof x === "string")) throw new Error(t("Arguments must be a JSON string array.", "参数必须是 JSON 字符串数组。"));
      if (!parsedEnv || Array.isArray(parsedEnv) || typeof parsedEnv !== "object") throw new Error(t("Environment mappings must be a JSON object.", "环境变量映射必须是 JSON 对象。"));
      void manage("save", editing, { ...draft, args: parsedArgs, env_from: parsedEnv as Record<string, string> });
    } catch (e) { setError(String(e)); }
  }
  const change = (key: keyof Server, value: string | number | boolean) => setDraft(current => current ? { ...current, [key]: value } : current);
  return <div className="mcp-manager">
    <div className="mcp-summary"><span>{t("Connected servers", "已连接服务")} <strong>{snapshot?.connected_servers ?? "—"}</strong></span><span>{t("External tools", "外部工具")} <strong>{snapshot?.remote_tool_count ?? "—"}</strong></span><button type="button" onClick={() => void refresh()} disabled={busy}>{t("Refresh", "刷新")}</button><button type="button" disabled={locked} onClick={() => open(blank())}>{t("Add server", "添加服务")}</button></div>
    {busy && <p role="status">{t("Updating MCP servers…", "正在更新 MCP 服务…")}</p>}
    {running && <p role="status">{t("Finish active turns before changing servers.", "请等待当前任务结束后修改服务。")}</p>}
    {!enabled && <p role="status">{t("MCP is disabled. Enable it to expose connected tools to the agent.", "MCP 已关闭，开启后才能向 AI 提供已连接工具。")}</p>}
    {snapshot && !snapshot.sdk_available && <p role="alert">{t("MCP SDK is not installed in this runtime. Install the MCP runtime dependency before connecting.", "当前运行时未安装 MCP SDK，需要安装 MCP 运行依赖才能连接。")}</p>}
    {error && <div className="mcp-message mcp-error" role="alert">{error}</div>}
    {notice && <div className="mcp-message" role="status">{notice}</div>}
    {draft && <form ref={formRef} className="settings-card mcp-form" onSubmit={save}>
      <h2>{editing ? t("Edit server", "编辑服务") : t("Add server", "添加服务")}</h2>
      <fieldset disabled={locked}>
        <label>{t("Name", "名称")}<input required pattern="[A-Za-z0-9][A-Za-z0-9_.-]{0,63}" maxLength={64} value={draft.name} disabled={!!editing} onChange={e => change("name", e.target.value)} /></label>
        <label>{t("Connection", "连接方式")}<select value={draft.transport} onChange={e => change("transport", e.target.value)}><option value="http">HTTP (Streamable HTTP)</option><option value="stdio">{t("Local process (stdio)", "本地进程（stdio）")}</option></select></label>
        {draft.transport === "http" ? <><label>{t("Server URL", "服务地址")}<input required type="url" placeholder="https://example.com/mcp" value={draft.url} onChange={e => change("url", e.target.value)} /></label><label>{t("Bearer token environment variable (optional)", "Bearer Token 环境变量名（可选）")}<input placeholder="GITHUB_TOKEN" value={draft.bearer_token_env.startsWith("LOOM_MCP_STORED_") ? "" : draft.bearer_token_env} onChange={e => change("bearer_token_env", e.target.value)} /></label><label>{t("Bearer token (OS credential store, optional)", "Bearer Token（系统凭据库，可选）")}<input type="password" autoComplete="new-password" value={token} placeholder={t("Leave blank to keep current credential", "留空保留现有凭据")} onChange={e => setToken(e.target.value)} /></label></> : <>
          <label>{t("Executable", "可执行程序")}<input required placeholder="uvx / npx / absolute path" value={draft.command} onChange={e => change("command", e.target.value)} /></label>
          <label>{t("Arguments (JSON array)", "启动参数（JSON 数组）")}<textarea value={args} onChange={e => setArgs(e.target.value)} spellCheck={false} /></label>
          <label>{t("Working directory (optional)", "工作目录（可选）")}<input value={draft.cwd} onChange={e => change("cwd", e.target.value)} /></label>
          <label>{t("Environment mappings (child variable → server variable)", "环境变量映射（子进程变量名 → 服务端变量名）")}<textarea placeholder={'{"API_KEY": "MY_API_KEY"}'} value={env} onChange={e => setEnv(e.target.value)} spellCheck={false} /></label>
        </>}
        <label>{t("Connection timeout (seconds)", "连接超时（秒）")}<input type="number" min={1} max={120} required value={draft.timeout_seconds} onChange={e => change("timeout_seconds", Number(e.target.value))} /></label>
        <label className="mcp-check"><input type="checkbox" checked={draft.enabled} onChange={e => change("enabled", e.target.checked)} />{t("Enable server", "启用服务")}</label>
        {draft.bearer_token_env.startsWith("LOOM_MCP_STORED_") && <div className="mcp-actions"><span>{t("A token is stored securely.", "Token 已保存在系统凭据库。")}</span><button type="button" onClick={() => change("bearer_token_env", "")}>{t("Remove stored token on save", "保存时移除已存 Token")}</button></div>}
      </fieldset>
      <p>{t("Store a bearer token in the OS credential store, or enter its environment variable name. Variables must be available to the Loom App Server; restart Loom after changing them. OAuth sign-in is not supported here. Local commands run on your computer when saved.", "Bearer Token 可保存在系统凭据库，也可填写环境变量名。变量须在 Loom App Server 环境中可用；修改变量后需重启 Loom。此处暂不支持 OAuth 登录。本地命令会在保存时运行。")}</p>
      <div className="mcp-actions"><button type="submit" disabled={locked}>{busy ? t("Connecting…", "连接中…") : t("Save and connect", "保存并连接")}</button><button type="button" disabled={busy} onClick={() => { setDraft(null); setToken(""); setError(""); }}>{t("Cancel", "取消")}</button></div>
    </form>}
    <section><h2>{t("Your servers", "已配置服务")}</h2>
      {snapshot?.servers.length === 0 && <div className="settings-card mcp-empty">{t("No servers yet. Choose a template below or add your own MCP server.", "尚未配置服务。选择下方模板或添加自己的 MCP 服务。")}</div>}
      {!snapshot && !error && <p role="status">{t("Loading…", "加载中…")}</p>}
      {snapshot?.servers.map(server => <article className="settings-card mcp-server" key={`${server.editable}-${server.name}`}>
        <div className="mcp-server-heading"><strong>{server.name}</strong><span data-connected={!!server.connected}>{!server.enabled ? t("Disabled", "已停用") : server.connected ? t("Connected", "已连接") : t("Not connected", "未连接")}</span></div>
        <p>{server.transport === "http" ? server.url : server.command} · {server.tool_count ?? 0} {t("tools", "个工具")}</p>
        {server.error && <p className="mcp-error">{server.error}</p>}
        {server.editable ? <div className="mcp-actions"><button disabled={locked} onClick={() => open(server, server.name)}>{t("Edit", "编辑")}</button><button disabled={locked} onClick={() => void manage("toggle", server.name)}>{server.enabled ? t("Disable", "停用") : t("Enable", "启用")}</button><button disabled={locked || !server.enabled} onClick={() => void manage("reconnect", server.name)}>{t("Reconnect / check", "重连 / 检查连接")}</button><button disabled={locked} onClick={() => setDeleting(server.name)}>{t("Delete", "删除")}</button></div> : <p>{t("Managed by runtime configuration. Edit the original configuration file.", "由运行时配置管理，请编辑原始配置文件。")}</p>}
        {deleting === server.name && <div className="mcp-actions"><span>{t("Delete this server?", "删除此服务？")}</span><button disabled={locked} onClick={() => void manage("delete", server.name)}>{t("Confirm delete", "确认删除")}</button><button disabled={busy} onClick={() => setDeleting("")}>{t("Cancel", "取消")}</button></div>}
        {snapshot.tools.some(tool => tool.server === server.name) && <details><summary>{t("Available tools", "可用工具")}</summary>{snapshot.tools.filter(tool => tool.server === server.name).map(tool => <div className="mcp-tool" key={tool.name}><strong>{tool.name}</strong><p>{tool.description}</p></div>)}</details>}
      </article>)}
    </section>
    <section><h2>{t("Quick setup", "快捷配置")}</h2><p>{t("Templates link to publisher instructions. Review requirements before connecting.", "模板附有发布者说明，请先查看依赖和授权要求。")}</p><input aria-label={t("Search templates", "搜索模板")} placeholder={t("Search templates…", "搜索模板…")} value={search} onChange={e => setSearch(e.target.value)} />
      <div className="mcp-presets">{presets.filter(p => `${p.name} ${p.description.join(" ")}`.toLowerCase().includes(search.toLowerCase())).map(p => <article className="settings-card" key={p.name}><h3>{p.name}</h3><p>{p.description[zh ? 1 : 0]}</p><div className="mcp-actions"><a href={p.docs} target="_blank" rel="noreferrer">{t("Publisher docs", "发布者文档")}</a><button disabled={locked} onClick={() => open({ ...blank(), ...p.server })}>{t("Configure", "配置")}</button></div></article>)}</div>
    </section>
  </div>;
}
