import { createPortal } from "react-dom";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Check, ChevronRight, Download, ExternalLink, Info, KeyRound, Loader2, Plus, RefreshCw, Search, X } from "./icons";
import { useI18n } from "../i18n";
import type { McpServer, McpSetupContext } from "./mcpTypes";

type Input = { key: string; label: string; description: string; required: boolean; default: string; placeholder: string; choices: string[]; format: string; environment: boolean };
type Variant = { id: string; kind: string; label: string; type: string; supported: boolean; reason: string; inputs: Input[]; required_auth: boolean; runtime: string };
type Entry = { name: string; version: string; title: string; description: string; publisher: string; website: string; repository: string; status: string; suggested_name: string; variants: Variant[]; compatible: boolean };
type Freshness = { cached: boolean; stale: boolean; fetched_at: number };
type Page = Freshness & { servers: Entry[]; next_cursor: string };
type Detail = Freshness & { server: Entry };
type Prepared = McpSetupContext & { server: McpServer };

const REASONS: Record<string, [string, string]> = {
  transport: ["This transport needs manual setup; Loom supports Streamable HTTP and stdio.", "此传输方式需手动配置，Loom 当前支持 Streamable HTTP 和 stdio。"],
  "custom-headers": ["Custom HTTP headers are not supported by this setup flow.", "当前配置流程暂不支持自定义 HTTP 请求头。"],
  authorization: ["This authorization scheme needs manual setup.", "此授权方式需要手动配置。"],
  "secret-argument": ["This recipe puts a secret in command arguments. Use publisher instructions and environment credentials instead.", "此配方使用命令行密钥参数，请参考发布者说明，改用环境变量凭据。"],
  "package-type": ["This package format requires manual installation.", "此安装包格式需要手动安装。"],
  "package-registry": ["Packages from this package registry require manual setup.", "此软件包源需要手动配置。"],
  "runtime-arguments": ["This package uses advanced launcher options; follow publisher instructions.", "此安装包使用高级启动参数，请参考发布者说明。"],
  "fixed-environment": ["This package needs fixed or templated environment values; follow publisher instructions.", "此安装包需要固定值或模板环境变量，请参考发布者说明。"],
  runtime: ["This launcher requires manual setup.", "此启动程序需要手动配置。"],
  checksum: ["This package requires additional checksum verification before installation.", "此安装包要求额外的校验步骤，需手动安装。"],
  inactive: ["This server version is not active in the registry.", "此服务版本在目录中并非启用状态。"],
  url: ["This endpoint cannot be configured automatically. Check publisher instructions.", "此服务地址无法自动配置，请查看发布者说明。"],
  "package-version": ["A supported, fixed package version is required.", "需要受支持的固定软件包版本。"],
  "package-identifier": ["This package identifier requires manual setup.", "此软件包标识需要手动配置。"],
  "unresolved-variable": ["This recipe contains unresolved variables.", "此配置包含无法解析的变量。"],
  "invalid-metadata": ["The publisher metadata cannot be translated into a Loom configuration.", "发布者提供的信息暂时无法转换为 Loom 配置。"],
};

export function McpMarketplace({ locked, installedNames, onInstall, onAddServer, onManage }: {
  locked: boolean;
  installedNames: string[];
  onInstall: (server: McpServer, context: McpSetupContext, token: string) => Promise<boolean>;
  onAddServer: () => void;
  onManage: () => void;
}) {
  const { language } = useI18n();
  const zh = language.startsWith("zh");
  const t = (en: string, cn: string) => zh ? cn : en;
  const reason = (code: string) => (REASONS[code] ?? REASONS["invalid-metadata"])[zh ? 1 : 0];
  const [query, setQuery] = useState("");
  const [activeQuery, setActiveQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState<Page | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [variantId, setVariantId] = useState("");
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [token, setToken] = useState("");
  const [tokenEnv, setTokenEnv] = useState("");
  const [pendingName, setPendingName] = useState("");
  const [selectedTitle, setSelectedTitle] = useState("");
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const operationSequence = useRef(0);
  const dialogRef = useRef<HTMLElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const preparing = pendingName !== "";
  const dialogOpen = selectedTitle !== "";
  const variant = detail?.server.variants.find(v => v.id === variantId);

  async function load(search: string, cursor = "", refresh = false) {
    const sequence = ++listSequence.current;
    setLoading(true); setError("");
    if (!cursor) { setPage(null); setActiveQuery(search); }
    try {
      const result = await window.loom.call<Page>("mcp/marketplace/list", { search, cursor, refresh });
      if (sequence !== listSequence.current) return;
      setPage(previous => {
        if (!cursor || !previous) return result;
        const names = new Set(previous.servers.map(s => s.name));
        return { ...result, servers: [...previous.servers, ...result.servers.filter(s => !names.has(s.name))],
          cached: previous.cached || result.cached, stale: previous.stale || result.stale,
          fetched_at: Math.min(previous.fetched_at, result.fetched_at), next_cursor: result.next_cursor === cursor ? "" : result.next_cursor };
      });
    } catch (e) { if (sequence === listSequence.current) setError(String(e)); }
    finally { if (sequence === listSequence.current) setLoading(false); }
  }
  useEffect(() => {
    void load("");
    return () => { listSequence.current++; detailSequence.current++; operationSequence.current++; };
  }, []);
  useEffect(() => {
    if (query.trim() === activeQuery) return;
    const timer = window.setTimeout(() => void load(query.trim()), 400);
    return () => window.clearTimeout(timer);
  }, [query, activeQuery]);

  function closeDialog() {
    if (preparing) return;
    detailSequence.current++; operationSequence.current++;
    setSelectedTitle(""); setDetail(null); setDetailLoading(false); setDetailError(""); setToken(""); setTokenEnv("");
  }
  useEffect(() => {
    if (!dialogOpen) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    return () => { document.body.style.overflow = overflow; returnFocus.current?.focus(); };
  }, [dialogOpen]);

  function selectVariant(choice: Variant) {
    setVariantId(choice.id); setDetailError(""); setToken(""); setTokenEnv("");
    setInputs(Object.fromEntries(choice.inputs.map(input => [input.key, input.default])));
  }
  async function inspect(entry: Entry, message = "") {
    const sequence = ++detailSequence.current;
    returnFocus.current = document.activeElement as HTMLElement | null;
    setSelectedTitle(entry.title || entry.name); setDetail(null); setDetailLoading(true); setDetailError(""); setToken(""); setTokenEnv("");
    try {
      const result = await window.loom.call<Detail>("mcp/marketplace/detail", { name: entry.name, version: entry.version });
      if (sequence !== detailSequence.current) return;
      setDetail(result);
      const first = result.server.variants.find(v => v.supported) ?? result.server.variants[0];
      if (first) selectVariant(first); else { setVariantId(""); setInputs({}); }
      setDetailError(message);
    } catch (e) { if (sequence === detailSequence.current) setDetailError(String(e)); }
    finally { if (sequence === detailSequence.current) setDetailLoading(false); }
  }
  async function installEntry(entry: Entry) {
    if (locked || preparing) return;
    const choices = entry.variants.filter(v => v.supported);
    const choice = choices[0];
    if (!choice || choices.length !== 1 || choice.inputs.length > 0 || choice.required_auth) {
      await inspect(entry); return;
    }
    const sequence = ++operationSequence.current;
    setPendingName(entry.name); setError("");
    try {
      const plan = await window.loom.call<Prepared>("mcp/marketplace/prepare", { name: entry.name, version: entry.version, variant: choice.id, inputs: {} });
      if (sequence !== operationSequence.current) return;
      if (plan.required_auth || (plan.runtime && !plan.runtime_available)) {
        setPendingName(""); await inspect(entry, plan.runtime && !plan.runtime_available ? t(`Install ${plan.runtime} on the App Server, then retry.`, `请先在 App Server 环境中安装 ${plan.runtime}，然后重试。`) : ""); return;
      }
      const ok = await onInstall(plan.server, plan, "");
      if (sequence === operationSequence.current && !ok) { setPendingName(""); await inspect(entry, t("Connection failed. Check authentication or the publisher instructions, then retry.", "连接失败，请检查授权或发布者说明后重试。")); }
    } catch (e) { if (sequence === operationSequence.current) setError(String(e)); }
    finally { if (sequence === operationSequence.current) setPendingName(""); }
  }
  async function installConfigured(event: FormEvent) {
    event.preventDefault();
    if (!detail || !variant?.supported || locked || preparing) return;
    if (variant.required_auth && !token.trim() && !tokenEnv.trim()) { setDetailError(t("Enter a bearer token or its environment variable name.", "请填写 Bearer Token 或对应的环境变量名。")); return; }
    const sequence = ++operationSequence.current;
    setPendingName(detail.server.name); setDetailError("");
    try {
      const plan = await window.loom.call<Prepared>("mcp/marketplace/prepare", { name: detail.server.name, version: detail.server.version, variant: variant.id, inputs });
      if (sequence !== operationSequence.current) return;
      if (plan.runtime && !plan.runtime_available) throw new Error(t(`Install ${plan.runtime} on the App Server, then retry.`, `请先在 App Server 环境中安装 ${plan.runtime}，然后重试。`));
      const ok = await onInstall({ ...plan.server, bearer_token_env: tokenEnv.trim() }, plan, token.trim());
      if (sequence !== operationSequence.current) return;
      if (ok) { setSelectedTitle(""); setDetail(null); setToken(""); setTokenEnv(""); }
      else setDetailError(t("The service did not connect. Check your settings and retry.", "服务尚未连接，请检查配置后重试。"));
    } catch (e) { if (sequence === operationSequence.current) setDetailError(String(e)); }
    finally { if (sequence === operationSequence.current) setPendingName(""); }
  }
  const visible = (page?.servers ?? []).filter(server => filter === "all" || (filter === "compatible" ? server.compatible : server.variants.some(v => v.type === filter && v.supported)));
  return <section className="mcp-marketplace" aria-label={t("MCP marketplace", "MCP 应用市场")}>
    <header className="mcp-market-heading"><div><h2>{t("Discover tools for your agent", "为 AI 添加更多工具")}</h2><p>{t("Find a service and install it here. Account details are only requested when needed.", "找到需要的服务，点击安装。需要账号或授权时，再补充相应信息。")}</p></div></header>
    <form className="mcp-market-search" onSubmit={e => { e.preventDefault(); void load(query.trim()); }}>
      <label className="mcp-search-field"><Search size={17} aria-hidden="true" /><span className="mcp-sr-only">{t("Search registry by name", "按名称搜索目录")}</span><input maxLength={200} type="search" placeholder={t("Search MCP servers…", "搜索 MCP 服务…")} value={query} onChange={e => setQuery(e.target.value)} /></label>
      <label><span className="mcp-sr-only">{t("Filter loaded results", "筛选已加载结果")}</span><select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">{t("All services", "全部服务")}</option><option value="compatible">{t("Ready to set up", "可安装服务")}</option><option value="http">{t("Remote HTTP", "远程 HTTP")}</option><option value="npm">Node.js / npm</option><option value="pypi">Python / PyPI</option></select></label>
      <button className="mcp-icon-button" disabled={loading || preparing} type="button" aria-label={t("Refresh directory", "刷新目录")} title={t("Refresh directory", "刷新目录")} onClick={() => void load(activeQuery, "", true)}><RefreshCw size={17} className={loading ? "mcp-spin" : ""} /></button>
      <button className="mcp-add-button" disabled={locked || preparing} type="button" onClick={onAddServer}><Plus size={17} />{t("Add server", "添加服务")}</button>
    </form>
    <div className="mcp-directory-meta"><span>{t("Official MCP Registry", "官方 MCP 目录")}{page ? ` · ${visible.length} ${t("shown", "项结果")}` : ""}</span><a href="https://registry.modelcontextprotocol.io" target="_blank" rel="noreferrer">{t("View directory", "查看目录")}<ExternalLink size={12} /></a></div>
    {page?.cached && <div className={page.stale ? "mcp-message" : "mcp-cache-note"} role="status">{page.stale ? t("Directory unavailable. Showing saved results from ", "目录暂时不可用，正在显示缓存，更新时间：") : t("Updated ", "更新于 ")}{new Date(page.fetched_at * 1000).toLocaleString(zh ? "zh-CN" : "en-US")}</div>}
    {error && <div className="mcp-message mcp-error" role="alert">{error}<button type="button" disabled={loading} onClick={() => void load(activeQuery, page?.next_cursor ?? "", true)}>{t("Retry", "重试")}</button></div>}
    {loading && !page && <div className="mcp-market-loading" role="status"><Loader2 className="mcp-spin" size={20} />{t("Loading services…", "正在加载服务…")}</div>}
    <div className="mcp-market-grid" aria-busy={loading}>{visible.map(entry => {
      const installed = installedNames.includes(entry.suggested_name);
      const pending = pendingName === entry.name;
      return <article className="settings-card mcp-market-card" key={entry.name}>
        <span className="mcp-market-monogram" aria-hidden="true">{entry.title.slice(0, 2).toUpperCase()}</span>
        <div className="mcp-market-card-content"><div className="mcp-market-title-line"><h3>{entry.title}</h3>{installed && <span className="mcp-installed-badge"><Check size={12} />{t("Installed", "已安装")}</span>}</div><div className="mcp-market-publisher">{entry.publisher}</div><p>{entry.description}</p><div className="mcp-market-tags">{Array.from(new Set(entry.variants.map(v => v.type))).map(type => <span key={type}>{type === "pypi" ? "Python" : type.toUpperCase()}</span>)}<span>v{entry.version}</span>{entry.variants.some(v => v.supported && v.required_auth) && <span><KeyRound size={11} />{t("Token required", "需要授权")}</span>}{!entry.compatible && <span>{t("Manual setup", "需手动配置")}</span>}</div></div>
        <div className="mcp-market-card-actions"><button className={installed ? "mcp-manage-button" : "mcp-install-button"} disabled={locked || preparing} type="button" onClick={() => installed ? onManage() : void installEntry(entry)}>{pending ? <Loader2 className="mcp-spin" size={16} /> : installed ? <Check size={16} /> : <Download size={16} />}{pending ? t("Installing…", "安装中…") : installed ? t("Manage", "管理") : entry.compatible ? t("Install", "安装") : t("Set up", "配置")}</button><button className="mcp-details-button" disabled={preparing} type="button" onClick={() => void inspect(entry)}>{t("Details", "详情")}<ChevronRight size={13} /></button></div>
      </article>;
    })}</div>
    {!loading && page && visible.length === 0 && <div className="settings-card mcp-empty">{page.servers.length > 0 ? t("No loaded services match this filter. Try another filter or load more.", "已加载的服务中没有匹配项，请更换筛选条件或加载更多。") : t("No services found. Try another name.", "没有找到服务，请尝试其他名称。")}</div>}
    {page?.next_cursor && <button className="mcp-load-more" disabled={loading || preparing} type="button" onClick={() => void load(activeQuery, page.next_cursor)}>{loading ? t("Loading…", "加载中…") : t("Load more services", "加载更多服务")}</button>}
    <p className="mcp-market-caption">{t("Services are provided by their publishers. Filters apply to loaded results.", "服务由各发布者提供，筛选作用于已加载的结果。")}</p>
    {dialogOpen && createPortal(<div className="mcp-manager mcp-dialog-backdrop settings-host" onMouseDown={event => { if (event.target === event.currentTarget) closeDialog(); }}><section ref={dialogRef} className="mcp-install-dialog" role="dialog" aria-modal="true" aria-labelledby="mcp-install-title" tabIndex={-1} onKeyDown={event => {
      if (event.key === "Escape") { event.stopPropagation(); closeDialog(); }
      if (event.key === "Tab") { const elements = [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])') ?? [])]; const first = elements[0], last = elements[elements.length - 1]; if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialogRef.current)) { event.preventDefault(); first?.focus(); } }
    }}>
      <header className="mcp-dialog-heading"><div><span className="mcp-dialog-eyebrow">{t("ADD TO LOOM", "添加到 LOOM")}</span><h2 id="mcp-install-title">{detail?.server.title ?? selectedTitle}</h2></div><button className="mcp-icon-button" type="button" disabled={preparing} aria-label={t("Close details", "关闭详情")} onClick={closeDialog}><X size={18} /></button></header>
      {detailLoading && <p className="mcp-dialog-loading" role="status"><Loader2 className="mcp-spin" size={18} />{t("Loading setup…", "正在加载安装配置…")}</p>}
      {detailError && <div className="mcp-message mcp-error" role="alert">{detailError}</div>}
      {detail && <><p>{detail.server.description}</p><div className="mcp-market-meta"><span>{detail.server.publisher}</span><span>v{detail.server.version}</span>{detail.server.website && <a target="_blank" rel="noreferrer" href={detail.server.website}>{t("Website", "发布者网站")} ↗</a>}{detail.server.repository && <a target="_blank" rel="noreferrer" href={detail.server.repository}>{t("Source", "源代码")} ↗</a>}</div>{detail.stale && <p>{t("Using cached service information.", "当前使用缓存的服务信息。")}</p>}
        {detail.server.variants.length > 0 ? <form className="mcp-market-setup" onSubmit={e => void installConfigured(e)}><fieldset disabled={preparing || locked}>
          <label>{t("Connection method", "连接方式")}<select value={variantId} onChange={e => { const choice = detail.server.variants.find(v => v.id === e.target.value); if (choice) selectVariant(choice); }}>{detail.server.variants.map(v => <option value={v.id} key={v.id}>{v.type.toUpperCase()} · {v.label || v.id}{v.supported ? "" : t(" · Manual setup", " · 需手动配置")}</option>)}</select></label>
          {variant?.supported ? <>
            {variant.runtime && <p className="mcp-runtime-note"><Info size={15} />{t("Requires ", "需要 ")}{variant.runtime === "npx" ? "Node.js / npx" : "uv / uvx"}{t(". Installation downloads and runs the specified package version.", "。安装时会下载并运行指定版本的软件包。")}</p>}
            {variant.inputs.map(input => <label key={input.key}>{input.environment ? t("Environment variable for ", "对应的环境变量名：") : ""}{input.label}{input.required ? " *" : ""}{input.choices.length ? <select required={input.required} value={inputs[input.key] ?? ""} onChange={e => setInputs(current => ({ ...current, [input.key]: e.target.value }))}><option value="">{t("Choose…", "请选择…")}</option>{input.choices.map(choice => <option key={choice}>{choice}</option>)}</select> : <input required={input.required} maxLength={4096} placeholder={input.placeholder} value={inputs[input.key] ?? ""} pattern={input.environment ? "[A-Za-z_][A-Za-z0-9_]*" : undefined} onChange={e => setInputs(current => ({ ...current, [input.key]: e.target.value }))} />}{input.description && <span className="mcp-input-hint">{input.description}</span>}{input.environment && <span className="mcp-input-hint">{t("Enter the variable name available to the App Server, not its value.", "请填写 App Server 环境中的变量名，不要填写密钥值。")}</span>}</label>)}
            {variant.type === "http" && <div className="mcp-auth-fields"><h3>{variant.required_auth ? t("Authentication required", "填写授权信息") : t("Authentication (optional)", "授权信息（可选）")}</h3><label>{t("Bearer token", "Bearer Token")}<input type="password" autoComplete="new-password" maxLength={16384} value={token} onChange={e => setToken(e.target.value)} placeholder={t("Saved securely in the system credential store", "安全保存到系统凭据库")} /></label><label>{t("Or use an environment variable", "或使用环境变量")}<input value={tokenEnv} onChange={e => setTokenEnv(e.target.value)} pattern="[A-Za-z_][A-Za-z0-9_]*" placeholder="MY_API_TOKEN" /></label></div>}
          </> : <p className="mcp-message">{reason(variant?.reason ?? "invalid-metadata")}</p>}
        </fieldset><footer className="mcp-dialog-footer"><button type="button" disabled={preparing} onClick={closeDialog}>{t("Cancel", "取消")}</button><button className="mcp-install-button" type="submit" disabled={locked || preparing || !variant?.supported}>{preparing ? <Loader2 className="mcp-spin" size={16} /> : <Download size={16} />}{preparing ? t("Installing…", "安装中…") : t("Install and connect", "安装并连接")}</button></footer></form> : <p>{t("No automatic setup is available. Follow the publisher's instructions.", "暂无自动安装方式，请参考发布者说明。")}</p>}
      </>}
    </section></div>, document.body)}
  </section>;
}
