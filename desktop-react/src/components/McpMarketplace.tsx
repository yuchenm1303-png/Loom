import { useEffect, useRef, useState, type FormEvent } from "react";
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

export function McpMarketplace({ locked, installedNames, onConfigure }: {
  locked: boolean;
  installedNames: string[];
  onConfigure: (server: McpServer, context: McpSetupContext) => void;
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
  const [preparing, setPreparing] = useState(false);
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const prepareSequence = useRef(0);
  const detailRef = useRef<HTMLElement>(null);

  async function load(search: string, cursor = "", refresh = false) {
    const sequence = ++listSequence.current;
    setLoading(true); setError("");
    if (!cursor) {
      setPage(null); setActiveQuery(search); setDetail(null); setDetailError("");
      detailSequence.current++; prepareSequence.current++; setDetailLoading(false); setPreparing(false);
    }
    try {
      const result = await window.loom.call<Page>("mcp/marketplace/list", { search, cursor, refresh });
      if (sequence !== listSequence.current) return;
      setPage(previous => {
        if (!cursor || !previous) return result;
        const names = new Set(previous.servers.map(s => s.name));
        return { ...result, servers: [...previous.servers, ...result.servers.filter(s => !names.has(s.name))],
          cached: previous.cached || result.cached, stale: previous.stale || result.stale, fetched_at: Math.min(previous.fetched_at, result.fetched_at),
          next_cursor: result.next_cursor === cursor ? "" : result.next_cursor };
      });
    } catch (e) { if (sequence === listSequence.current) setError(String(e)); }
    finally { if (sequence === listSequence.current) setLoading(false); }
  }
  useEffect(() => {
    void load("");
    return () => { listSequence.current++; detailSequence.current++; prepareSequence.current++; };
  }, []);

  function selectVariant(variant: Variant) {
    prepareSequence.current++; setPreparing(false); setVariantId(variant.id); setDetailError("");
    setInputs(Object.fromEntries(variant.inputs.map(input => [input.key, input.default])));
  }
  async function inspect(entry: Entry) {
    const sequence = ++detailSequence.current;
    prepareSequence.current++; setPreparing(false); setDetail(null); setDetailLoading(true); setDetailError("");
    try {
      const result = await window.loom.call<Detail>("mcp/marketplace/detail", { name: entry.name, version: entry.version });
      if (sequence !== detailSequence.current) return;
      setDetail(result);
      const first = result.server.variants.find(v => v.supported) ?? result.server.variants[0];
      if (first) selectVariant(first); else { setVariantId(""); setInputs({}); }
    } catch (e) { if (sequence === detailSequence.current) setDetailError(String(e)); }
    finally { if (sequence === detailSequence.current) setDetailLoading(false); }
  }
  useEffect(() => { if (detail) detailRef.current?.scrollIntoView({ block: "nearest" }); }, [detail]);
  const variant = detail?.server.variants.find(v => v.id === variantId);
  async function prepare(event: FormEvent) {
    event.preventDefault();
    if (!detail || !variant?.supported || locked) return;
    const sequence = ++prepareSequence.current;
    setPreparing(true); setDetailError("");
    try {
      const result = await window.loom.call<Prepared>("mcp/marketplace/prepare", {
        name: detail.server.name, version: detail.server.version, variant: variant.id, inputs,
      });
      if (sequence === prepareSequence.current) onConfigure(result.server, result);
    } catch (e) { if (sequence === prepareSequence.current) setDetailError(String(e)); }
    finally { if (sequence === prepareSequence.current) setPreparing(false); }
  }

  const visible = (page?.servers ?? []).filter(server => filter === "all" || (filter === "compatible" ? server.compatible : server.variants.some(v => v.type === filter && v.supported)));
  return <section className="mcp-marketplace" aria-label={t("MCP marketplace", "MCP 应用市场")}>
    <header className="mcp-market-heading"><div><h2>{t("Discover MCP servers", "发现 MCP 服务")}</h2><p>{t("Browse the Official MCP Registry, then review a connection plan before adding it to Loom.", "浏览官方 MCP 目录，检查连接配置后添加到 Loom。")}</p></div><a href="https://registry.modelcontextprotocol.io" target="_blank" rel="noreferrer">{t("Official registry", "官方目录")} ↗</a></header>
    <form className="mcp-market-search" onSubmit={e => { e.preventDefault(); void load(query.trim()); }}>
      <label><span className="mcp-sr-only">{t("Search registry by name", "按名称搜索目录")}</span><input maxLength={200} type="search" placeholder={t("Search server names, e.g. github…", "搜索服务名称，例如 github…")} value={query} onChange={e => setQuery(e.target.value)} /></label>
      <button disabled={loading} type="submit">{t("Search", "搜索")}</button>
      <button disabled={loading} type="button" onClick={() => void load(activeQuery, "", true)}>{t("Refresh directory", "刷新目录")}</button>
      <label><span className="mcp-sr-only">{t("Filter loaded results", "筛选已加载结果")}</span><select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">{t("All results", "全部结果")}</option><option value="compatible">{t("Compatible with Loom", "Loom 可配置")}</option><option value="http">{t("Remote HTTP", "远程 HTTP")}</option><option value="npm">npm</option><option value="pypi">Python / PyPI</option></select></label>
    </form>
    <p className="mcp-market-caption">{t("Listings are provided by publishers; inclusion in the directory is not a Loom endorsement. Filters apply to loaded results.", "服务信息由发布者提供，目录收录不代表 Loom 认证。筛选作用于已加载的结果。")}</p>
    {page?.cached && <div className="mcp-message" role="status">{page.stale ? t("Directory unavailable. Showing saved results from ", "目录暂时不可用，正在显示缓存，更新时间：") : t("Cached directory · updated ", "目录缓存 · 更新时间：")}{new Date(page.fetched_at * 1000).toLocaleString(zh ? "zh-CN" : "en-US")}</div>}
    {error && <div className="mcp-message mcp-error" role="alert">{t("Could not load the directory. Retry, or use quick setup/manual configuration below.", "目录加载失败，请重试，或使用快捷配置和手动添加。")}<p>{error}</p><button type="button" disabled={loading} onClick={() => void load(activeQuery, page?.next_cursor ?? "", true)}>{t("Retry", "重试")}</button></div>}
    {loading && <p role="status">{t("Loading directory…", "正在加载目录…")}</p>}
    {detailLoading && <p role="status">{t("Loading server details…", "正在加载服务详情…")}</p>}
    {detailError && <div className="mcp-message mcp-error" role="alert">{detailError}</div>}
    {detail && <section ref={detailRef} className="settings-card mcp-market-detail" aria-label={t("Server details", "服务详情")}>
      <div className="mcp-server-heading"><h3>{detail.server.title}</h3><button type="button" disabled={preparing} onClick={() => { setDetail(null); setDetailError(""); detailSequence.current++; prepareSequence.current++; }}>{t("Close details", "关闭详情")}</button></div>
      <code>{detail.server.name}</code><p>{detail.server.description}</p>
      <div className="mcp-market-meta"><span>{t("Publisher", "发布者")}: {detail.server.publisher}</span><span>{t("Version", "版本")}: {detail.server.version}</span><span>{detail.server.status}</span></div>
      <div className="mcp-actions">{detail.server.website && <a target="_blank" rel="noreferrer" href={detail.server.website}>{t("Publisher website", "发布者网站")} ↗</a>}{detail.server.repository && <a target="_blank" rel="noreferrer" href={detail.server.repository}>{t("Source repository", "源代码仓库")} ↗</a>}</div>
      {detail.stale && <p role="status">{t("These details are cached; the registry is currently unavailable.", "当前详情来自缓存，目录暂时不可用。")}</p>}
      {detail.server.variants.length > 0 ? <form className="mcp-market-setup" onSubmit={e => void prepare(e)}>
        <label>{t("Installation method", "安装方式")}<select disabled={preparing} value={variantId} onChange={e => { const choice = detail.server.variants.find(v => v.id === e.target.value); if (choice) selectVariant(choice); }}>{detail.server.variants.map(v => <option value={v.id} key={v.id}>{v.type.toUpperCase()} · {v.label || v.id}{v.supported ? "" : t(" · Manual setup", " · 需手动配置")}</option>)}</select></label>
        {variant?.supported ? <>
          {variant.runtime && <p>{t("Requires ", "需要 ")}{variant.runtime === "npx" ? "Node.js / npx" : "uv / uvx"}{t(". Saving the reviewed configuration will download and run this publisher's pinned package version.", "。保存经过检查的配置后，将下载并运行发布者指定的固定软件包版本。")}</p>}
          {variant.required_auth && <p>{t("A bearer token is required. You will enter it securely in the next step.", "需要 Bearer Token，可在下一步安全填写。")}</p>}
          {variant.inputs.map(input => <label key={input.key}>{input.environment ? t("Parent environment variable for ", "对应服务端环境变量名：") : ""}{input.label}{input.required ? " *" : ""}
            {input.choices.length > 0 ? <select required={input.required} value={inputs[input.key] ?? ""} disabled={preparing} onChange={e => setInputs(current => ({ ...current, [input.key]: e.target.value }))}><option value="">{t("Choose…", "请选择…")}</option>{input.choices.map(choice => <option key={choice}>{choice}</option>)}</select> : <input required={input.required} maxLength={4096} disabled={preparing} placeholder={input.placeholder} value={inputs[input.key] ?? ""} pattern={input.environment ? "[A-Za-z_][A-Za-z0-9_]*" : undefined} onChange={e => setInputs(current => ({ ...current, [input.key]: e.target.value }))} />}
            {input.description && <span className="mcp-input-hint">{input.description}</span>}
            {input.environment && <span className="mcp-input-hint">{t("Enter the variable name available to the App Server, never its secret value.", "填写 App Server 环境中可用的变量名，不要填写密钥值。")}</span>}
          </label>)}
          <button type="submit" disabled={locked || preparing || installedNames.includes(detail.server.suggested_name)}>{preparing ? t("Preparing…", "正在生成配置…") : installedNames.includes(detail.server.suggested_name) ? t("Already configured", "已配置") : t("Review configuration", "检查并添加配置")}</button>
        </> : <p className="mcp-message">{reason(variant?.reason ?? "invalid-metadata")}</p>}
      </form> : <p>{t("No installation recipe is available. Follow publisher instructions and add a server manually.", "尚无可用的安装配方，请参考发布者说明手动添加服务。")}</p>}
    </section>}
    <div className="mcp-market-grid" aria-busy={loading}>{visible.map(entry => <article className="settings-card mcp-market-card" key={entry.name}>
      <div className="mcp-market-card-top"><span className="mcp-market-monogram" aria-hidden="true">{entry.title.slice(0, 2).toUpperCase()}</span><div><h3>{entry.title}</h3><span className="mcp-input-hint">{entry.publisher}</span></div></div>
      <p>{entry.description}</p><code>{entry.name}</code>
      <div className="mcp-market-tags">{Array.from(new Set(entry.variants.map(v => v.type))).map(type => <span key={type}>{type.toUpperCase()}</span>)}<span>{entry.version}</span></div>
      <div className="mcp-market-card-bottom"><span className="mcp-input-hint">{installedNames.includes(entry.suggested_name) ? t("Configured", "已配置") : entry.compatible ? t("Setup available", "可配置") : t("Manual setup", "需手动配置")}</span><button type="button" onClick={() => void inspect(entry)}>{t("Details", "详情")}</button></div>
    </article>)}</div>
    {!loading && page && visible.length === 0 && <div className="settings-card mcp-empty">{page.servers.length > 0 ? t("No loaded results match this filter. Choose another filter or load more.", "已加载结果中没有匹配项，请更换筛选条件或加载更多。") : t("No servers found. Try a shorter name or another keyword.", "没有找到服务，请尝试更短的名称或其他关键词。")}</div>}
    {page?.next_cursor && <button className="mcp-load-more" disabled={loading} type="button" onClick={() => void load(activeQuery, page.next_cursor)}>{t("Load more servers", "加载更多服务")}</button>}
  </section>;
}
