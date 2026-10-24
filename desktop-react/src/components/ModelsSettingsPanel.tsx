import {
  Activity,
  BrainCircuit,
  Check,
  ChevronRight,
  CircleAlert,
  Cpu,
  Eye,
  Gauge,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Server,
  Sparkles,
  Trash2,
  Wifi,
  X,
  Zap,
} from "./icons";
import { useEffect, useMemo, useRef, useState } from "react";
import { useI18n } from "../i18n";
import type {
  AddModelInput,
  EditModelInput,
  ModelProfile,
  ModelRestartResult,
  ModelSnapshot,
  ModelTestResult,
  ReasoningUpdateResult,
} from "../types/loom";
import "./settings-models-manager.css";
import "./settings-models-workspace.css";
import { groupModelProfiles } from "./modelProviderGroups";

type Filter = "all" | "builtin" | "saved";
type FormMode = "add" | "edit" | null;

type ModelFormState = {
  selection: string;
  name: string;
  adapter: "openai" | "openai-compatible";
  baseUrl: string;
  model: string;
  apiKey: string;
  vision: boolean | null;
};

interface ModelsSettingsPanelProps {
  initialSnapshot: ModelSnapshot | null;
  runtimeModel?: string;
  running: boolean;
  onSnapshot?(snapshot: ModelSnapshot): void;
}

const EMPTY_FORM: ModelFormState = {
  selection: "",
  name: "",
  adapter: "openai-compatible",
  baseUrl: "",
  model: "",
  apiKey: "",
  vision: null,
};

const COPY = {
  en: {
    eyebrow: "Inference",
    title: "Models",
    subtitle: "Manage providers and choose the model used for new chats.",
    refresh: "Refresh",
    add: "Add connection",
    activeModel: "Default model",
    ready: "Ready",
    managed: "Managed",
    saved: "Saved",
    builtin: "Built-in",
    modelId: "Model ID",
    endpoint: "Endpoint",
    provider: "Provider",
    reasoning: "Reasoning",
    defaultReasoning: "Default reasoning",
    test: "Test connection",
    testing: "Testing…",
    copyEndpoint: "Copy endpoint",
    profiles: "Model profiles",
    profilesCaption: "Manage saved connections. Changes are available when no task is running.",
    search: "Search name, model ID, or endpoint",
    all: "All",
    builtinFilter: "Built-in",
    savedFilter: "Saved",
    setActive: "Use as default",
    current: "Current",
    edit: "Edit",
    delete: "Delete",
    confirmDelete: "Confirm delete",
    testOk: "Connection healthy",
    modelListed: "Model advertised by endpoint",
    modelNotListed: "Endpoint reachable; model ID was not listed",
    latency: "latency",
    discovered: "models discovered",
    recent: "Recent model IDs",
    recentCaption: "Use another model ID with the current connection.",
    switch: "Switch model ID",
    noRecent: "No recent model IDs yet.",
    addTitle: "Add model connection",
    editTitle: "Edit model connection",
    name: "Connection name",
    apiType: "API type",
    baseUrl: "Base URL",
    apiKey: "API key",
    apiKeyEdit: "API key (leave blank to keep current key)",
    vision: "Vision input",
    visionDesc: "Declare whether this endpoint accepts image inputs.",
    cancel: "Cancel",
    saveUse: "Save & use",
    saveChanges: "Save changes",
    keyNote: "Keys are stored in the operating-system credential store and are never returned to the renderer.",
    activeEdit: "Switch away from this profile before editing it.",
    turnLocked: "Finish or stop the active turn before switching or changing model configuration.",
    noProfiles: "No profiles match this filter.",
    chat: "Chat",
    stream: "Streaming",
    capabilityVision: "Vision",
    capabilityReasoning: "Reasoning",
  },
  zh: {
    eyebrow: "推理配置",
    title: "模型",
    subtitle: "按提供商管理连接，选择新对话使用的模型。",
    refresh: "刷新",
    add: "添加模型",
    activeModel: "默认模型",
    ready: "可用",
    managed: "托管",
    saved: "已保存",
    builtin: "内置",
    modelId: "模型 ID",
    endpoint: "接口地址",
    provider: "提供商",
    reasoning: "思考强度",
    defaultReasoning: "默认思考强度",
    test: "测试连接",
    testing: "检测中…",
    copyEndpoint: "复制地址",
    profiles: "模型配置",
    profilesCaption: "管理已保存连接，当前任务结束后可修改。",
    search: "搜索名称、模型 ID 或接口地址",
    all: "全部",
    builtinFilter: "内置",
    savedFilter: "自定义",
    setActive: "设为默认",
    current: "当前",
    edit: "编辑",
    delete: "删除",
    confirmDelete: "确认删除",
    testOk: "连接正常",
    modelListed: "接口已返回该模型",
    modelNotListed: "接口可访问，但模型列表中未发现该 ID",
    latency: "延迟",
    discovered: "个模型",
    recent: "最近使用的模型 ID",
    recentCaption: "使用当前连接，切换其他模型 ID。",
    switch: "切换模型 ID",
    noRecent: "暂无最近使用的模型 ID。",
    addTitle: "添加模型连接",
    editTitle: "编辑模型连接",
    name: "连接名称",
    apiType: "API 类型",
    baseUrl: "接口地址",
    apiKey: "API 密钥",
    apiKeyEdit: "API 密钥（留空保留现有密钥）",
    vision: "视觉输入",
    visionDesc: "声明这个接口是否接受图片输入。",
    cancel: "取消",
    saveUse: "保存并使用",
    saveChanges: "保存修改",
    keyNote: "API Key 保存在操作系统凭据库中，不会返回到前端界面。",
    activeEdit: "请先切换到其他模型，再编辑当前配置。",
    turnLocked: "请先结束或停止当前 Agent 任务，再切换或修改模型配置。",
    noProfiles: "没有符合当前筛选条件的模型。",
    chat: "对话",
    stream: "流式",
    capabilityVision: "视觉",
    capabilityReasoning: "推理",
  },
} as const;

function adapterLabel(value: string): string {
  if (value === "openai") return "OpenAI";
  if (value === "opencode-go") return "OpenCode Go";
  if (value === "opencode-console") return "OpenCode Console";
  return "OpenAI Compatible";
}

export function ModelsSettingsPanel({ initialSnapshot, runtimeModel, running, onSnapshot }: ModelsSettingsPanelProps) {
  const { language } = useI18n();
  const c = language === "zh-CN" ? COPY.zh : COPY.en;
  const [snapshot, setSnapshot] = useState<ModelSnapshot | null>(initialSnapshot);
  const [busy, setBusy] = useState("");
  const [query, setQuery] = useState("");
  const [selectedProvider, setSelectedProvider] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [formMode, setFormMode] = useState<FormMode>(null);
  const [form, setForm] = useState<ModelFormState>(EMPTY_FORM);
  const [catalog, setCatalog] = useState<{ models: string[]; latencyMs: number } | null>(null);
  const discoveryRequest = useRef(0);
  const [magpieForm, setMagpieForm] = useState(false);
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState("");
  const [tests, setTests] = useState<Record<string, ModelTestResult>>({});
  const [testErrors, setTestErrors] = useState<Record<string, string>>({});
  const [otherModel, setOtherModel] = useState("");
  const modalRef = useRef<HTMLElement>(null);
  const busyRef = useRef(busy);
  busyRef.current = busy;

  useEffect(() => {
    if (!formMode) return;
    const previous = document.activeElement as HTMLElement | null;
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault();
        event.stopPropagation();
        setFormMode(null);
      }
      if (event.key !== "Tab") return;
      const items = modalRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]');
      if (!items?.length) return;
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    document.addEventListener("keydown", handleKey, true);
    return () => { document.removeEventListener("keydown", handleKey, true); previous?.focus(); };
  }, [formMode]);

  useEffect(() => setSnapshot(initialSnapshot), [initialSnapshot]);
  useEffect(() => {
    discoveryRequest.current += 1;
    setCatalog(null);
  }, [form.baseUrl, form.apiKey, form.adapter, formMode]);

  const current = snapshot?.current;
  const profiles = snapshot?.profiles ?? [];
  const currentModel = current?.model || runtimeModel || "—";

  const providerGroups = useMemo(() => groupModelProfiles(profiles), [profiles]);
  const filteredGroups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return providerGroups.map(group => ({ ...group, profiles: group.profiles.filter(profile =>
      (filter === "all" || profile.kind === filter) && (!needle ||
        [group.name, profile.name, profile.model, profile.baseUrl, profile.adapter]
          .some(value => value.toLowerCase().includes(needle))))
    })).filter(group => group.profiles.length);
  }, [providerGroups, filter, query]);
  const selectedGroup = filteredGroups.find(group => group.id === selectedProvider)
    || filteredGroups.find(group => group.profiles.some(profile => profile.selection === current?.selection))
    || filteredGroups[0];
  const visibleProfiles = selectedGroup?.profiles ?? [];
  const providerSync = snapshot?.catalogSync?.providers.find(provider => provider.groupId === selectedGroup?.id);

  function commit(next: ModelSnapshot) {
    setSnapshot(next);
    onSnapshot?.(next);
  }

  async function refresh() {
    setBusy("refresh");
    setError("");
    try {
      commit(await window.loom.listModels<ModelSnapshot>(true));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  async function activate(profile: ModelProfile) {
    if (running || profile.available === false || busy || profile.selection === current?.selection) return;
    setBusy(`activate:${profile.selection}`);
    setError("");
    try {
      const result = await window.loom.switchModelProfile<ModelRestartResult>(profile.selection);
      commit(result.models);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  async function test(profile: ModelProfile) {
    if (busy) return;
    setBusy(`test:${profile.selection}`);
    setTests(currentTests => { const next = { ...currentTests }; delete next[profile.selection]; return next; });
    setTestErrors((currentErrors) => ({ ...currentErrors, [profile.selection]: "" }));
    try {
      const result = await window.loom.testModel<ModelTestResult>(profile.selection);
      setTests((currentTests) => ({ ...currentTests, [profile.selection]: result }));
    } catch (cause) {
      setTestErrors((currentErrors) => ({
        ...currentErrors,
        [profile.selection]: cause instanceof Error ? cause.message : String(cause),
      }));
    } finally {
      setBusy("");
    }
  }

  function openAdd() {
    setMagpieForm(false);
    setForm({ ...EMPTY_FORM });
    setFormMode("add");
    setError("");
  }

  function openEdit(profile: ModelProfile) {
    setMagpieForm(false);
    if (profile.kind !== "saved" || profile.selection === current?.selection) return;
    setForm({
      selection: profile.selection,
      name: profile.name,
      adapter: profile.adapter === "openai" ? "openai" : "openai-compatible",
      baseUrl: profile.baseUrl,
      model: profile.model,
      apiKey: "",
      vision: profile.vision ?? null,
    });
    setFormMode("edit");
    setError("");
  }

  function openMagpie() {
    setMagpieForm(true);
    setForm({ ...EMPTY_FORM, name: "Magpie", baseUrl: "http://127.0.0.1:3425/v1", apiKey: "magpie" });
    setFormMode("add");
    setError("");
  }

  async function discover() {
    if (busy || running) return;
    const request = ++discoveryRequest.current;
    setBusy("discover");
    setError("");
    setCatalog(null);
    try {
      const result = await window.loom.discoverModels({ baseUrl: form.baseUrl.trim(), apiKey: form.apiKey.trim() });
      if (request === discoveryRequest.current) setCatalog(result);
    } catch (cause) {
      if (request === discoveryRequest.current) setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  async function submitForm() {
    if (running || busy) return;
    if (!form.name.trim() || !form.model.trim() || (form.adapter === "openai-compatible" && !form.baseUrl.trim())) {
      setError(language === "zh-CN" ? "请完整填写连接名称、模型 ID 和 Base URL。" : "Complete the connection name, model ID, and Base URL.");
      return;
    }
    if (formMode === "add" && !form.apiKey.trim()) {
      setError(language === "zh-CN" ? "新增连接需要 API Key。" : "An API key is required for a new connection.");
      return;
    }
    setBusy(formMode === "edit" ? `edit:${form.selection}` : "add");
    setError("");
    try {
      const payload: AddModelInput = {
        name: form.name.trim(),
        adapter: form.adapter,
        baseUrl: form.adapter === "openai" ? "" : form.baseUrl.trim(),
        model: form.model.trim(),
        apiKey: form.apiKey.trim(),
        vision: form.vision,
      };
      if (formMode === "edit") {
        const next = await window.loom.updateModel<ModelSnapshot>({ ...payload, selection: form.selection } satisfies EditModelInput);
        commit(next);
      } else {
        const result = await window.loom.addModel<ModelRestartResult>(payload as unknown as Record<string, unknown>);
        commit(result.models);
      }
      setFormMode(null);
      setForm({ ...EMPTY_FORM });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  async function remove(profile: ModelProfile) {
    if (profile.kind !== "saved" || running || busy) return;
    if (confirmDelete !== profile.selection) {
      setConfirmDelete(profile.selection);
      return;
    }
    setBusy(`delete:${profile.selection}`);
    setError("");
    try {
      const result = await window.loom.deleteModel<ModelRestartResult>(profile.selection);
      commit(result.models);
      setConfirmDelete("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  async function changeReasoning(value: string) {
    const reasoning = snapshot?.current?.reasoning;
    if (!reasoning || running || busy || value === reasoning.value) return;
    setBusy("reasoning");
    setError("");
    try {
      const result = await window.loom.setReasoning<ReasoningUpdateResult>(reasoning.kind, value);
      commit(result.models);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  async function switchOtherModel(model = otherModel) {
    const value = model.trim();
    if (running || !value || busy) return;
    setBusy("other-model");
    setError("");
    try {
      const result = await window.loom.switchCurrentModel<ModelRestartResult>(value);
      commit(result.models);
      setOtherModel("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  const currentTest = current ? tests[current.selection] : undefined;
  const currentTestError = current ? testErrors[current.selection] : "";
  const currentReasoning = current?.reasoning;
  const recent = (snapshot?.recentModels ?? []).filter((value) => value && value !== currentModel).slice(0, 8);

  return (
    <div className="models-settings-manager">
      <div className="models-settings-heading">
        <div>
          <h1>{c.title}</h1>
          <p>{c.subtitle}</p>
        </div>
        <div className="models-heading-actions">
          <button type="button" className="mature-action-button" onClick={() => void refresh()} disabled={Boolean(busy)}>
            <RefreshCw size={14} spinning={busy === "refresh"} />{c.refresh}
          </button>
          <button type="button" className="models-primary-action" onClick={openAdd} disabled={running || Boolean(busy)}>
            <Plus size={14} />{c.add}
          </button>
          <button type="button" className="mature-action-button" onClick={openMagpie} disabled={running || Boolean(busy)}>
            <Server size={14} />{language === "zh-CN" ? "连接 Magpie" : "Connect Magpie"}
          </button>
        </div>
      </div>

      {running ? (
        <div className="models-lock-banner"><CircleAlert size={15} /><span>{c.turnLocked}</span></div>
      ) : null}
      {error ? <div className="models-error-banner"><CircleAlert size={15} /><span>{error}</span></div> : null}

      <section className="models-section">
        <div className="models-section-heading"><h2>{c.activeModel}</h2></div>
        <div className="models-active-card">
          <div className="models-active-main">
            <span className="models-active-icon"><BrainCircuit size={24} strokeWidth={1.7} /></span>
            <div className="models-active-copy">
              <div className="models-active-title-line">
                <span className="settings-eyebrow">{current?.kind === "saved" ? c.saved : c.managed}</span>
                <span className={`models-ready-badge ${current?.available === false ? "is-unavailable" : ""}`}>
                  <i />{current?.available === false ? (language === "zh-CN" ? "已失效" : "Unavailable") : c.ready}
                </span>
              </div>
              <strong>{current?.name || currentModel}</strong>
              <span>{adapterLabel(current?.provider || current?.adapter || "openai-compatible")}</span>
            </div>
            {current ? (
              <button type="button" className="models-test-button" onClick={() => void test(current)} disabled={Boolean(busy)}>
                <Wifi size={14} />{busy === `test:${current.selection}` ? c.testing : c.test}
              </button>
            ) : null}
          </div>

          <details className="models-current-details"><summary>{language === "zh-CN" ? "连接详情与模型能力" : "Connection details & capabilities"}</summary>
          <div className="models-active-meta">
            <div><span>{c.modelId}</span><code>{currentModel}</code></div>
            <div><span>{c.endpoint}</span><code title={current?.baseUrl || ""}>{current?.baseUrl || "OpenAI default"}</code></div>
            <div><span>{c.provider}</span><strong>{adapterLabel(current?.provider || current?.adapter || "openai-compatible")}</strong></div>
            <div><span>{c.reasoning}</span><strong>{currentReasoning?.options.find((option) => option.value === currentReasoning.value)?.label || currentReasoning?.value || "—"}</strong></div>
          </div>

          <div className="models-capability-row">
            <span><Activity size={12} />{c.chat}</span>
            <span><Zap size={12} />{c.stream}</span>
            <span title={current?.imageInput?.source}><Eye size={12} />{current?.vision === true ? c.capabilityVision : current?.vision === false ? (language === "zh-CN" ? "不支持图片" : "Images unsupported") : (language === "zh-CN" ? "图片能力未确认" : "Image support unknown")}</span>
            {(currentTest?.capabilities.reasoning ?? Boolean(currentReasoning)) ? <span><Sparkles size={12} />{c.capabilityReasoning}</span> : null}
          </div>

          </details>
          {currentReasoning ? (
            <div className="models-reasoning-row">
              <div><Gauge size={15} /><span><strong>{c.defaultReasoning}</strong><small>{currentReasoning.source}</small></span></div>
              <select value={currentReasoning.value} disabled={running || Boolean(busy)} onChange={(event) => void changeReasoning(event.target.value)}>
                {currentReasoning.options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
              </select>
            </div>
          ) : null}

          {(currentTest || currentTestError) ? (
            <div className={`models-test-result ${currentTestError ? "error" : "success"}`}>
              {currentTestError ? <CircleAlert size={14} /> : <Check size={14} />}
              <div>
                <strong>{currentTestError || c.testOk}</strong>
                {currentTest ? <span>{currentTest.modelListed ? c.modelListed : c.modelNotListed} · {currentTest.latencyMs}ms {c.latency} · {currentTest.discoveredModels} {c.discovered}</span> : null}
              </div>
            </div>
          ) : null}
        </div>
      </section>

      <section className="models-section">

        <div className="models-toolbar">
          <label className="models-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} aria-label={c.search} placeholder={c.search} /></label>
          <div className="models-filter" role="group" aria-label="Model profile filter">
            {(["all", "builtin", "saved"] as const).map((value) => (
              <button type="button" key={value} className={filter === value ? "active" : ""} aria-pressed={filter === value} onClick={() => { setFilter(value); setConfirmDelete(""); }}>
                {value === "all" ? c.all : value === "builtin" ? c.builtinFilter : c.savedFilter}
              </button>
            ))}
          </div>
        </div>

        <div className="models-provider-workspace">
          <nav className="models-provider-nav" aria-label={c.provider}>
            <div className="models-provider-nav-label">{c.provider}<span>{filteredGroups.length}</span></div>
            {filteredGroups.map(group => <button type="button" key={group.id} aria-current={selectedGroup?.id === group.id ? "true" : undefined}
              className={selectedGroup?.id === group.id ? "selected" : ""} onClick={() => { setSelectedProvider(group.id); setConfirmDelete(""); }}>
              <Server size={17} /><span><strong>{group.name}</strong><small>{group.endpoint}</small></span>
              <span className="models-provider-count">{group.profiles.length}</span>
            </button>)}
            {!filteredGroups.length ? <p>{c.noProfiles}</p> : null}
          </nav>
          <div className="models-provider-content">
            {selectedGroup ? <header className="models-provider-header">
              <div><span className="settings-eyebrow">{selectedGroup.profiles[0].kind === "saved" ? c.saved : c.managed}</span><h2>{selectedGroup.name}</h2>
                <p>{selectedGroup.profiles.length} {language === "zh-CN" ? "个模型 · 选择模型用于新对话" : "models · Choose a default for new chats"}</p></div>
              <span className="models-kind-badge">{selectedGroup.profiles[0].kind === "saved" ? c.savedFilter : c.builtinFilter}</span>
            </header> : null}
            {selectedGroup ? <details className="models-provider-connection"><summary><Server size={14} />{language === "zh-CN" ? "连接与接口信息" : "Connection & endpoint"}<ChevronRight size={14} /></summary>
              <dl><div><dt>{c.endpoint}</dt><dd>{selectedGroup.profiles[0].baseUrl || "https://api.openai.com/v1"}</dd></div>
              <div><dt>{c.apiType}</dt><dd>{adapterLabel(selectedGroup.profiles[0].adapter)}</dd></div></dl>
              <p><KeyRound size={13} />{language === "zh-CN" ? "密钥安全保存。自定义连接可在对应模型的编辑入口中更新。" : "Credentials are stored securely. Edit a saved model to update its connection."}</p>
            </details> : null}
            {providerSync && providerSync.state !== "synced" && providerSync.state !== "unsupported" ? <div className="models-lock-banner" role="status"><CircleAlert size={15} /><span>{providerSync.message || (language === "zh-CN" ? "模型目录暂未同步，显示已保存的列表。可刷新重试。" : "Catalog sync is unavailable. Showing saved models; refresh to retry.")}</span></div> : null}
            {snapshot?.catalogMetadataError ? <div className="models-error-banner" role="status">{snapshot.catalogMetadataError}</div> : null}
        <div className="models-profile-grid">
          {visibleProfiles.map((profile) => {
            const active = profile.selection === current?.selection;
            const testResult = tests[profile.selection];
            const testError = testErrors[profile.selection];
            const isTesting = busy === `test:${profile.selection}`;
            const isSwitching = busy === `activate:${profile.selection}`;
            const isDeleting = busy === `delete:${profile.selection}`;
            return (
              <article className={`models-profile-card ${active ? "active" : ""}`} key={profile.selection}>
                <div className="models-profile-head">
                  <span className="models-profile-icon"><Cpu size={17} /></span>
                  <div className="models-profile-title"><strong>{profile.name}</strong><span title={profile.model}>{profile.model}</span></div>
                  <span className={`models-kind-badge ${profile.kind}`}>{profile.kind === "saved" ? c.saved : c.builtin}</span>
                </div>
                <div className="models-capability-row compact">
                  <span>{c.chat}</span><span>{c.stream}</span>
                  <span title={profile.imageInput?.source}>{profile.vision === true ? c.capabilityVision : profile.vision === false ? (language === "zh-CN" ? "不支持图片" : "Images unsupported") : (language === "zh-CN" ? "图片能力未确认" : "Image support unknown")}</span>
                  {(testResult?.capabilities.reasoning ?? Boolean(profile.reasoning)) ? <span>{c.capabilityReasoning}</span> : null}
                </div>
                {profile.available === false ? <div className="models-unavailable-reason"><CircleAlert size={13} /><span>{profile.statusMessage || profile.catalogMessage || (language === "zh-CN" ? "提供商暂未提供该模型，请刷新目录或检查连接。" : "This model is unavailable. Refresh the catalog or check its connection.")}</span></div> : null}
                {(testResult || testError) ? (
                  <div className={`models-card-diagnostic ${testError ? "error" : "success"}`}>
                    <i />
                    <span>{testError || `${testResult?.latencyMs ?? "—"}ms · ${testResult?.discoveredModels ?? 0} ${c.discovered}`}</span>
                  </div>
                ) : null}
                <div className="models-profile-actions">
                  <button
                    type="button"
                    className={active ? "active" : ""}
                    disabled={running || profile.available === false || Boolean(busy) || active}
                    title={profile.available === false ? (profile.statusMessage || "No longer advertised by the provider") : undefined}
                    onClick={() => void activate(profile)}
                  >
                    {isSwitching ? <RefreshCw size={13} spinning /> : active ? <Check size={13} /> : <ChevronRight size={13} />}
                    {profile.available === false ? (language === "zh-CN" ? "已失效" : "Unavailable") : active ? c.current : c.setActive}
                  </button>
                  <button type="button" disabled={Boolean(busy)} onClick={() => void test(profile)}><Wifi size={13} />{isTesting ? c.testing : c.test}</button>
                  {profile.kind === "saved" ? (
                    <>
                      <button type="button" disabled={profile.available === false || running || Boolean(busy) || active} title={active ? c.activeEdit : c.edit} onClick={() => openEdit(profile)}><Pencil size={13} />{c.edit}</button>
                      <button type="button" className={confirmDelete === profile.selection ? "danger confirm" : "danger"} disabled={running || Boolean(busy)} onClick={() => void remove(profile)}>
                        {isDeleting ? <RefreshCw size={13} spinning /> : <Trash2 size={13} />}
                        {confirmDelete === profile.selection ? c.confirmDelete : c.delete}
                      </button>
                    </>
                  ) : null}
                </div>
              </article>
            );
          })}
          {!visibleProfiles.length ? <div className="models-empty-state">{c.noProfiles}</div> : null}
        </div>
          </div>
        </div>
      </section>

      <details className="models-section models-advanced-section">
        <summary>{c.recent}</summary>
        <p>{c.recentCaption}</p>
        <div className="models-recent-panel">
          <div className="models-other-model-row">
            <input value={otherModel} onChange={(event) => setOtherModel(event.target.value)} placeholder="gpt-5 / claude / qwen / ..." disabled={running || Boolean(busy)} />
            <button type="button" disabled={running || !otherModel.trim() || Boolean(busy)} onClick={() => void switchOtherModel()}><RefreshCw size={13} />{c.switch}</button>
          </div>
          {recent.length ? <div className="models-recent-chips">{recent.map((model) => <button type="button" key={model} disabled={running || Boolean(busy)} onClick={() => void switchOtherModel(model)}>{model}</button>)}</div> : <span className="models-recent-empty">{c.noRecent}</span>}
        </div>
      </details>

      {formMode ? (
        <div className="models-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setFormMode(null); }}>
          <section ref={modalRef} className="models-modal" role="dialog" aria-modal="true" aria-label={formMode === "add" ? c.addTitle : c.editTitle}>
            <div className="models-modal-head">
              <div><span className="models-modal-icon">{formMode === "add" ? <Plus size={17} /> : <Pencil size={17} />}</span><div><strong>{formMode === "add" ? c.addTitle : c.editTitle}</strong><span>{adapterLabel(form.adapter)}</span></div></div>
              <button type="button" aria-label={c.cancel} onClick={() => setFormMode(null)}><X size={16} /></button>
            </div>
            <div className="models-form-grid">
              {magpieForm ? <p className="wide">{language === "zh-CN"
                ? "先启动 Magpie 并登录模型账号，再获取模型列表。本机默认密钥为 magpie；连接其他机器时，请填写该网关的地址和密钥。"
                : "Start Magpie and sign in to a provider, then fetch its models. The local default key is magpie; for another machine, enter its gateway address and key."}</p> : null}
              <label className="wide"><span>{c.name}</span><input autoFocus value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
              <label><span>{c.apiType}</span><select value={form.adapter} onChange={(event) => setForm({ ...form, adapter: event.target.value as ModelFormState["adapter"] })}><option value="openai-compatible">OpenAI Compatible</option><option value="openai">OpenAI</option></select></label>
              <label><span>{c.modelId}</span><input value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} /></label>
              <label className="wide"><span>{c.baseUrl}</span><input value={form.baseUrl} disabled={form.adapter === "openai"} placeholder={form.adapter === "openai" ? "OpenAI default" : "https://api.example.com/v1"} onChange={(event) => setForm({ ...form, baseUrl: event.target.value })} /></label>
              <label className="wide"><span>{formMode === "edit" ? c.apiKeyEdit : c.apiKey}</span><div className="models-secret-field"><KeyRound size={14} /><input type="password" value={form.apiKey} onChange={(event) => setForm({ ...form, apiKey: event.target.value })} /></div></label>
              {form.adapter === "openai-compatible" ? <div className="wide">
                <button type="button" className="mature-action-button" disabled={Boolean(busy) || running || !form.baseUrl.trim() || !form.apiKey.trim()} onClick={() => void discover()}>
                  <RefreshCw size={14} spinning={busy === "discover"} />{language === "zh-CN" ? "获取模型列表" : "Fetch models"}
                </button>
                {catalog ? <p role="status">{catalog.models.length
                  ? `${catalog.models.length} ${c.discovered} · ${catalog.latencyMs}ms`
                  : language === "zh-CN" ? "连接成功，但没有可用模型。请在 Magpie 中登录账号并启用模型。" : "Connected, but no models are available. Sign in and enable models in Magpie."}</p> : null}
                {catalog?.models.length ? <label><span>{language === "zh-CN" ? "选择网关模型" : "Choose gateway model"}</span><select aria-label={language === "zh-CN" ? "选择网关模型" : "Choose gateway model"} value={catalog.models.includes(form.model) ? form.model : ""} onChange={(event) => setForm({ ...form, model: event.target.value, vision: null })}>
                  <option value="" disabled>{language === "zh-CN" ? "请选择模型" : "Select a model"}</option>
                  {catalog.models.map((model) => <option key={model} value={model}>{model}</option>)}
                </select></label> : null}
              </div> : null}
              <div className="models-form-switch wide"><div><Eye size={15} /><span><strong>{c.vision}</strong><small>{c.visionDesc}</small></span></div><select aria-label={c.vision} value={form.vision === null ? "unknown" : String(form.vision)} onChange={(event) => setForm({ ...form, vision: event.target.value === "unknown" ? null : event.target.value === "true" })}><option value="unknown">{language === "zh-CN" ? "未确认" : "Unknown"}</option><option value="true">{language === "zh-CN" ? "支持" : "Supported"}</option><option value="false">{language === "zh-CN" ? "不支持" : "Unsupported"}</option></select></div>
            </div>
            <div className="models-key-note"><KeyRound size={13} /><span>{c.keyNote}</span></div>
            {error ? <div className="models-error-banner compact"><CircleAlert size={14} /><span>{error}</span></div> : null}
            <div className="models-modal-actions"><button type="button" onClick={() => setFormMode(null)}>{c.cancel}</button><button type="button" className="primary" disabled={Boolean(busy) || running} onClick={() => void submitForm()}>{busy.startsWith(formMode === "edit" ? "edit:" : "add") ? <RefreshCw size={13} spinning /> : formMode === "edit" ? <Check size={13} /> : <Plus size={13} />}{formMode === "edit" ? c.saveChanges : c.saveUse}</button></div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
