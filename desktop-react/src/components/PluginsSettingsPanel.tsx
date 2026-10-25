import { useEffect, useRef, useState, type FormEvent } from "react";
import { useI18n } from "../i18n";
import "./settings-mcp.css";
import "./settings-plugins.css";

type Source = string | { url: string; path?: string; ref?: string };
type Skill = { name: string; description: string; kind?: string };
type Server = { name: string; enabled?: boolean; command?: string; args?: string[]; url?: string; connected?: boolean; error?: string; tool_count?: number };
type Plugin = { name: string; version: string; description: string; enabled: boolean; status: string; source: Source; revision?: string; error?: string; activeSkills?: number; skills: Skill[]; servers: Server[]; credentials: { name: string; stored: boolean }[]; unsupported: string[] };
type Preview = { token: string; name: string; version: string; description: string; source: Source; integrity: string; gitRevision: string; format: string; skills: Skill[]; servers: Server[]; credentials: string[]; unsupported: string[]; alreadyInstalled: boolean; updateFor?: string };
type CatalogEntry = { name: string; description: string; category: string; source: Source | null; reviewable: boolean; homepage: string };
type Catalog = { plugins: CatalogEntry[]; total: number; nextOffset: number | null; stale: boolean; cached: boolean; fetchedAt: number };

export function PluginsSettingsPanel({ running }: { running: boolean }) {
  const { language } = useI18n();
  const zh = language.startsWith("zh");
  const t = (en: string, cn: string) => zh ? cn : en;
  const [tab, setTab] = useState<"installed" | "directory" | "source">("installed");
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [source, setSource] = useState("");
  const [subpath, setSubpath] = useState("");
  const [ref, setRef] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [allowPartial, setAllowPartial] = useState(false);
  const [enableOnInstall, setEnableOnInstall] = useState(true);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [credentialEditor, setCredentialEditor] = useState<Plugin | null>(null);
  const [removeCredentials, setRemoveCredentials] = useState<string[]>([]);
  const [removing, setRemoving] = useState("");
  const [query, setQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState("");
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const catalogSequence = useRef(0);
  const mounted = useRef(true);
  const reviewRef = useRef<HTMLElement>(null);
  const locked = running || busy || loading;

  async function refresh() {
    setLoading(true); setError("");
    try { const result = await window.loom.call<{ plugins: Plugin[] }>("plugin/list", {}); if (mounted.current) setPlugins(result.plugins); }
    catch (e) { if (mounted.current) setError(String(e)); }
    finally { if (mounted.current) setLoading(false); }
  }
  useEffect(() => { mounted.current = true; void refresh(); return () => { mounted.current = false; catalogSequence.current++; }; }, []);
  useEffect(() => { if (preview || credentialEditor) reviewRef.current?.scrollIntoView({ block: "start" }); }, [preview, credentialEditor]);

  async function inspect(params: Record<string, unknown>) {
    setBusy(true); setError(""); setNotice(""); setSecrets({}); setCredentialEditor(null); setRemoveCredentials([]); setRemoving("");
    try {
      if (preview) await window.loom.call("plugin/manage", { action: "discard", token: preview.token });
      setPreview(null);
      const result = await window.loom.call<Preview>("plugin/preview", params);
      if (!mounted.current) { void window.loom.call("plugin/manage", { action: "discard", token: result.token }).catch(() => {}); return; }
      setPreview(result); setAllowPartial(false); setEnableOnInstall(plugins.find(p => p.name === result.name)?.enabled ?? true);
    } catch (e) { if (mounted.current) setError(String(e)); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function mutate(params: Record<string, unknown>) {
    if (locked) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const result = await window.loom.call<{ plugins: Plugin[] }>("plugin/manage", params);
      if (!mounted.current) return;
      setPlugins(result.plugins); setPreview(null); setCredentialEditor(null); setSecrets({}); setRemoveCredentials([]); setRemoving(""); setTab("installed");
      setNotice(t("Plugin configuration applied. Changes are available to the next agent request.", "插件配置已生效，下一次 AI 请求即可使用更新后的能力。"));
    } catch (e) { if (mounted.current) setError(String(e)); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function cancelPreview() {
    if (!preview) return;
    setBusy(true);
    try { await window.loom.call("plugin/manage", { action: "discard", token: preview.token }); }
    catch (e) { setError(String(e)); }
    finally { setPreview(null); setSecrets({}); setBusy(false); }
  }
  async function loadCatalog(search: string, offset = 0, force = false) {
    const sequence = ++catalogSequence.current;
    setCatalogLoading(true); setCatalogError("");
    if (offset === 0) { setCatalog(null); setActiveQuery(search); }
    try {
      const result = await window.loom.call<Catalog>("plugin/catalog", { search, offset, refresh: force });
      if (sequence === catalogSequence.current) setCatalog(previous => offset && previous ? { ...result, plugins: [...previous.plugins, ...result.plugins], stale: previous.stale || result.stale } : result);
    } catch (e) { if (sequence === catalogSequence.current) setCatalogError(String(e)); }
    finally { if (sequence === catalogSequence.current) setCatalogLoading(false); }
  }
  function sourceSubmit(event: FormEvent) {
    event.preventDefault(); void inspect({ source: { url: source.trim(), path: subpath.trim(), ref: ref.trim() } });
  }
  const secretValues = () => Object.fromEntries(Object.entries(secrets).filter(([, value]) => value.trim()));
  const sourceText = (value: Source) => typeof value === "string" ? value : [value.url, value.path, value.ref].filter(Boolean).join(" · ");
  const status = (value: string) => ({ Active: t("Active", "已启用"), Disabled: t("Disabled", "已停用"), Degraded: t("Needs attention", "需要配置"), Error: t("Error", "加载失败"), "Needs reinstall": t("Needs reinstall", "需要重新安装"), Discovered: t("Not installed", "尚未安装") }[value] ?? value);
  const credentialFields = (names: string[]) => names.map(name => <label key={name}>{name}<input aria-label={name} type="password" autoComplete="new-password" maxLength={16384} value={secrets[name] ?? ""} placeholder={t("Leave blank to keep the saved value or use the server environment", "留空保留已保存凭据，或使用服务端环境变量")} onChange={e => setSecrets(current => ({ ...current, [name]: e.target.value }))} /></label>);

  return <div className="mcp-manager plugin-manager">
    <div className="settings-page-heading settings-heading-with-action"><div><h1>{t("Plugins", "插件")}</h1><p>{t("Install reusable bundles of skills, command workflows and MCP tools.", "安装包含 Skills、命令工作流和 MCP 工具的扩展包。")}</p></div><button type="button" disabled={busy || loading} onClick={() => void refresh()}>{t("Refresh", "刷新")}</button></div>
    <div className="plugin-view-switch" aria-label={t("Plugin views", "插件视图")}>{([['installed', t("Installed", "已安装")], ['directory', t("Discover", "发现插件")], ['source', t("Install from source", "从来源安装")]] as const).map(([key, label]) => <button key={key} aria-pressed={tab === key} disabled={busy} type="button" onClick={() => { setTab(key); if (key === "directory" && !catalog && !catalogLoading) void loadCatalog(""); }}>{label}</button>)}</div>
    {running && <p role="status">{t("You can inspect packages while a task runs. Finish active turns before applying changes.", "任务运行时可以检查插件，安装或修改需等待当前任务结束。")}</p>}
    {busy && <p role="status">{t("Preparing or applying plugin changes…", "正在准备或应用插件变更…")}</p>}
    {error && <div className="mcp-message mcp-error" role="alert">{error}</div>}
    {notice && <div className="mcp-message" role="status">{notice}</div>}
    {preview && <section ref={reviewRef} className="settings-card plugin-review" aria-label={t("Review plugin", "检查插件")}>
      <h2>{preview.name} <small>{preview.version}</small></h2><p>{preview.description}</p><p>{sourceText(preview.source)}</p><p className="plugin-fingerprint">{t("Snapshot", "内容快照")}: {preview.integrity}{preview.gitRevision && <> · Git {preview.gitRevision}</>}</p>
      <p>{preview.skills.length} {t("skills / command workflows", "项 Skill／命令工作流")} · {preview.servers.length} {t("MCP servers", "个 MCP 服务")}</p>
      <ul className="plugin-components">{preview.skills.map(skill => <li key={skill.name}><strong>{skill.name}</strong><span>{skill.description}</span></li>)}{preview.servers.map(server => <li key={server.name}><strong>{server.name}</strong><code>{server.url || `${server.command} ${JSON.stringify(server.args ?? [])}`}</code></li>)}</ul>
      <form onSubmit={e => { e.preventDefault(); void mutate({ action: preview.alreadyInstalled || preview.updateFor ? "upgrade" : "install", name: preview.updateFor || preview.name, token: preview.token, enabled: enableOnInstall, allowPartial, credentials: secretValues() }); }}>
        <fieldset disabled={busy}>
          {preview.credentials.length > 0 && <><h3>{t("Credentials", "凭据")}</h3><p>{t("Values are stored in the OS credential store and never written to plugin files. Leaving a field empty uses an existing credential or an App Server environment variable.", "填写的值会保存在系统凭据库，不会写入插件文件。留空则使用已有凭据或 App Server 环境变量。")}</p>{credentialFields(preview.credentials)}</>}
          {preview.unsupported.length > 0 && <div className="mcp-message"><p>{t("These components cannot run in Loom: ", "以下组件暂不能在 Loom 中运行：")}{preview.unsupported.join(", ")}</p><label className="plugin-check"><input type="checkbox" checked={allowPartial} onChange={e => setAllowPartial(e.target.checked)} />{t("Install supported components only", "仅安装受支持的组件")}</label></div>}
          <label className="plugin-check"><input type="checkbox" checked={enableOnInstall} onChange={e => setEnableOnInstall(e.target.checked)} />{t("Enable after installation", "安装后启用")}</label>
        </fieldset>
        <p>{t("Enabling starts bundled MCP services. Skills and bundled scripts continue to use Loom's normal permissions; installation does not run install hooks.", "启用后会启动插件中的 MCP 服务。Skills 和随附脚本仍受 Loom 权限约束，安装过程不运行安装钩子。")}</p>
        <div className="mcp-actions"><button type="submit" disabled={locked || (preview.unsupported.length > 0 && !allowPartial)}>{preview.alreadyInstalled || preview.updateFor ? t("Apply reviewed update", "应用检查后的更新") : t("Install plugin", "安装插件")}</button><button type="button" disabled={busy} onClick={() => void cancelPreview()}>{t("Cancel", "取消")}</button></div>
      </form>
    </section>}
    {credentialEditor && <section ref={reviewRef} className="settings-card plugin-review"><h2>{credentialEditor.name} · {t("Credentials", "凭据")}</h2><form onSubmit={e => { e.preventDefault(); void mutate({ action: "configure", name: credentialEditor.name, credentials: secretValues(), removeCredentials }); }}><fieldset disabled={busy}>{credentialFields(credentialEditor.credentials.map(c => c.name))}{credentialEditor.credentials.filter(c => c.stored).map(c => <label className="plugin-check" key={c.name}><input type="checkbox" checked={removeCredentials.includes(c.name)} onChange={e => setRemoveCredentials(current => e.target.checked ? [...current, c.name] : current.filter(n => n !== c.name))} />{t("Remove saved value for ", "删除已保存的凭据：")}{c.name}</label>)}</fieldset><div className="mcp-actions"><button disabled={locked} type="submit">{t("Save credentials and reconnect", "保存凭据并重新连接")}</button><button type="button" disabled={busy} onClick={() => { setCredentialEditor(null); setSecrets({}); }}>{t("Cancel", "取消")}</button></div></form></section>}
    {tab === "installed" && <section><h2>{t("Installed plugins", "已安装插件")}</h2>{loading && <p role="status">{t("Loading plugins…", "正在加载插件…")}</p>}{!loading && plugins.length === 0 && <div className="settings-card mcp-empty"><h3>{t("Add your first plugin", "添加第一个插件")}</h3><p>{t("Discover an open-source bundle, inspect a local folder or ZIP, or try the included skill-only example.", "可以发现开源插件、检查本地目录或 ZIP，也可以试用内置的 Skill 示例。")}</p><button disabled={busy} type="button" onClick={() => void inspect({ source: "builtin:review-assistant" })}>{t("Preview example plugin", "预览示例插件")}</button></div>}
      {plugins.map(plugin => <article className="settings-card plugin-installed" key={plugin.name}><div className="mcp-server-heading"><h3>{plugin.name} <small>{plugin.version}</small></h3><span>{status(plugin.status)}</span></div><p>{plugin.description}</p>{plugin.error && <p className="mcp-error">{plugin.error}</p>}<p>{plugin.activeSkills ?? 0} {t("active skills", "项可用 Skill")} · {plugin.servers.filter(s => s.connected).length}/{plugin.servers.filter(s => s.enabled !== false).length} {t("connected servers", "个已连接服务")}</p>
        <div className="mcp-actions">{plugin.status === "Discovered" ? <button disabled={busy} type="button" onClick={() => void inspect({ source: plugin.source })}>{t("Review installation", "检查并安装")}</button> : <><button disabled={locked || !!preview || !!credentialEditor || (plugin.status === "Needs reinstall" && !plugin.enabled)} type="button" onClick={() => void mutate({ action: plugin.enabled ? "disable" : "enable", name: plugin.name })}>{plugin.enabled ? t("Disable", "停用") : t("Enable", "启用")}</button><button disabled={locked || !plugin.enabled || !!preview || !!credentialEditor} type="button" onClick={() => void mutate({ action: "reconnect", name: plugin.name })}>{t("Reconnect", "重新连接")}</button><button disabled={busy} type="button" onClick={() => void inspect({ name: plugin.name })}>{t("Review update", "检查更新")}</button>{plugin.credentials.length > 0 && <button disabled={busy || !!preview} type="button" onClick={() => { setCredentialEditor(plugin); setSecrets({}); setRemoveCredentials([]); }}>{t("Configure credentials", "配置凭据")}</button>}<button disabled={locked || !!preview || !!credentialEditor} type="button" onClick={() => setRemoving(plugin.name)}>{t("Uninstall", "卸载")}</button></>}</div>
        {removing === plugin.name && <div className="mcp-message"><p>{t("Remove this plugin and its saved credentials? Your original source folder stays intact.", "卸载此插件并删除其保存的凭据？原始来源文件夹会保留。")}</p><div className="mcp-actions"><button disabled={locked} type="button" onClick={() => void mutate({ action: "remove", name: plugin.name })}>{t("Confirm uninstall", "确认卸载")}</button><button disabled={busy} type="button" onClick={() => setRemoving("")}>{t("Cancel", "取消")}</button></div></div>}
        <details><summary>{t("Components and source", "组件与来源")}</summary><p>{sourceText(plugin.source)}</p><ul className="plugin-components">{plugin.skills.map(skill => <li key={skill.name}><strong>{skill.name}</strong><span>{skill.description}</span></li>)}{plugin.servers.map(server => <li key={server.name}><strong>{server.name}</strong><span>{server.enabled === false ? t("Disabled", "已停用") : server.connected ? t("Connected", "已连接") : t("Not connected", "未连接")} · {server.tool_count ?? 0} {t("tools", "个工具")}</span>{server.error && <span className="mcp-error">{server.error}</span>}</li>)}</ul>{plugin.unsupported.length > 0 && <p>{t("Excluded components: ", "未加载的组件：")}{plugin.unsupported.join(", ")}</p>}</details>
      </article>)}
    </section>}
    {tab === "source" && <section className="settings-card plugin-review"><h2>{t("Install from source", "从来源安装")}</h2><form onSubmit={sourceSubmit}><fieldset disabled={busy}><label>{t("Folder, ZIP or HTTPS Git URL", "目录、ZIP 或 HTTPS Git 地址")}<input required value={source} onChange={e => setSource(e.target.value)} placeholder="C:\\Plugins\\my-plugin / https://github.com/owner/repo" /></label><div className="mcp-actions"><button type="button" onClick={() => { void window.loom.pickDirectory().then(value => { if (value) setSource(value); }).catch(e => setError(String(e))); }}>{t("Choose folder", "选择目录")}</button><button type="button" onClick={() => { void window.loom.pickFiles().then(values => { if (values[0]) setSource(values[0]); }).catch(e => setError(String(e))); }}>{t("Choose ZIP", "选择 ZIP")}</button></div><label>{t("Plugin subdirectory (optional)", "插件子目录（可选）")}<input value={subpath} onChange={e => setSubpath(e.target.value)} placeholder="plugins/my-plugin" /></label><label>{t("Git branch, tag or commit (optional)", "Git 分支、标签或提交（可选）")}<input value={ref} onChange={e => setRef(e.target.value)} /></label></fieldset><p>{t("Supports Loom manifests, Claude-style skill/MCP bundles, and Agent Skills collections. Packages are copied and inspected before activation.", "支持 Loom 清单、Claude 风格的 Skill／MCP 插件包和 Agent Skills 集合。激活前会复制并检查包内容。")}</p><button type="submit" disabled={busy}>{t("Inspect package", "检查插件包")}</button></form></section>}
    {tab === "directory" && <section className="plugin-directory"><h2>{t("Discover plugins", "发现插件")}</h2><p>{t("Browse Anthropic's public plugin directory. Compatibility is checked against the downloaded package; being listed does not guarantee Loom compatibility.", "浏览 Anthropic 的公开插件目录。实际兼容性会在下载并检查插件包后确认，目录收录不代表适用于 Loom。")}</p><form className="mcp-market-search" onSubmit={e => { e.preventDefault(); void loadCatalog(query.trim()); }}><label><span className="mcp-sr-only">{t("Search plugins", "搜索插件")}</span><input value={query} maxLength={200} placeholder={t("Search by name, category or description…", "按名称、分类或描述搜索…")} onChange={e => setQuery(e.target.value)} /></label><button disabled={catalogLoading} type="submit">{t("Search", "搜索")}</button><button disabled={catalogLoading} type="button" onClick={() => void loadCatalog(activeQuery, 0, true)}>{t("Refresh directory", "刷新目录")}</button></form>
      {catalogLoading && <p role="status">{t("Loading directory…", "正在加载目录…")}</p>}{catalogError && <div className="mcp-message mcp-error" role="alert">{catalogError}<button disabled={catalogLoading} type="button" onClick={() => void loadCatalog(activeQuery)}>{t("Retry", "重试")}</button></div>}{catalog?.stale && <div className="mcp-message" role="status">{t("Directory unavailable; showing cached entries from ", "目录暂时不可用，正在显示缓存，更新时间：")}{new Date(catalog.fetchedAt * 1000).toLocaleString(zh ? "zh-CN" : "en-US")}</div>}
      <div className="mcp-market-grid">{catalog?.plugins.map(entry => <article className="settings-card mcp-market-card" key={entry.name}><h3>{entry.name}</h3><p>{entry.description}</p><span className="mcp-input-hint">{entry.category}</span><div className="mcp-actions">{entry.homepage && <a href={entry.homepage} target="_blank" rel="noreferrer">{t("Publisher", "发布者网站")} ↗</a>}<button disabled={busy || !entry.reviewable} type="button" onClick={() => void inspect({ catalogName: entry.name })}>{entry.reviewable ? t("Inspect package", "检查插件包") : t("Manual setup", "需手动配置")}</button></div></article>)}</div>
      {catalog && !catalogLoading && catalog.plugins.length === 0 && <p>{t("No plugins match this search.", "没有找到匹配的插件。")}</p>}{catalog?.nextOffset !== null && catalog?.nextOffset !== undefined && <button disabled={catalogLoading} type="button" onClick={() => void loadCatalog(activeQuery, catalog.nextOffset ?? 0)}>{t("Load more", "加载更多")}</button>}
    </section>}
  </div>;
}
