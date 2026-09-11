import {
  Activity,
  BrainCircuit,
  Check,
  ChevronRight,
  CircleAlert,
  Copy,
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
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
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

type Filter = "all" | "builtin" | "saved";
type FormMode = "add" | "edit" | null;

type ModelFormState = {
  selection: string;
  name: string;
  adapter: "openai" | "openai-compatible";
  baseUrl: string;
  model: string;
  apiKey: string;
  vision: boolean;
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
  vision: true,
};

const COPY = {
  en: {
    eyebrow: "Inference",
    title: "Models",
    subtitle: "Manage model connections, defaults, reasoning, diagnostics, and provider capabilities.",
    refresh: "Refresh",
    add: "Add connection",
    activeModel: "Active model",
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
    profilesCaption: "Switch, diagnose, edit, or remove model connections. Switching and configuration changes are locked while an agent turn is active.",
    search: "Search name, model ID, or endpoint",
    all: "All",
    builtinFilter: "Built-in",
    savedFilter: "Saved",
    setActive: "Set active",
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
    recentCaption: "Reuse another model ID on the current API connection without creating a duplicate profile.",
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
    subtitle: "统一管理模型连接、默认模型、思考强度、连接诊断和 Provider 能力。",
    refresh: "刷新",
    add: "添加模型",
    activeModel: "当前模型",
    ready: "可用",
    managed: "托管",
    saved: "已保存",
    builtin: "内置",
    modelId: "模型 ID",
    endpoint: "接口地址",
    provider: "Provider",
    reasoning: "思考强度",
    defaultReasoning: "默认思考强度",
    test: "测试连接",
    testing: "检测中…",
    copyEndpoint: "复制地址",
    profiles: "模型配置",
    profilesCaption: "可以切换、检测、编辑或删除模型连接。Agent 正在运行时会锁定切换和配置修改。",
    search: "搜索名称、模型 ID 或接口地址",
    all: "全部",
    builtinFilter: "内置",
    savedFilter: "自定义",
    setActive: "设为当前",
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
    recentCaption: "在当前 API 连接上直接切换其他 Model ID，不需要重复创建配置。",
    switch: "切换 Model ID",
    noRecent: "暂时没有最近使用的 Model ID。",
    addTitle: "添加模型连接",
    editTitle: "编辑模型连接",
    name: "连接名称",
    apiType: "API 类型",
    baseUrl: "Base URL",
    apiKey: "API Key",
    apiKeyEdit: "API Key（留空则保留当前密钥）",
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
  return value === "openai" ? "OpenAI" : "OpenAI Compatible";
}

function endpointLabel(value: string): string {
  if (!value) return "OpenAI default";
  try {
    return new URL(value).host;
  } catch {
    return value;
  }
}

function copyToClipboard(value: string): Promise<void> {
  return navigator.clipboard?.writeText ? navigator.clipboard.writeText(value) : Promise.resolve();
}

export function ModelsSettingsPanel({ initialSnapshot, runtimeModel, running, onSnapshot }: ModelsSettingsPanelProps) {
  const { language } = useI18n();
  const c = language === "zh-CN" ? COPY.zh : COPY.en;
  const [snapshot, setSnapshot] = useState<ModelSnapshot | null>(initialSnapshot);
  const [busy, setBusy] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [formMode, setFormMode] = useState<FormMode>(null);
  const [form, setForm] = useState<ModelFormState>(EMPTY_FORM);
  const [error, setError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState("");
  const [tests, setTests] = useState<Record<string, ModelTestResult>>({});
  const [testErrors, setTestErrors] = useState<Record<string, string>>({});
  const [otherModel, setOtherModel] = useState("");

  useEffect(() => setSnapshot(initialSnapshot), [initialSnapshot]);

  const current = snapshot?.current;
  const profiles = snapshot?.profiles ?? [];
  const currentModel = current?.model || runtimeModel || "—";

  const visibleProfiles = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return profiles.filter((profile) => {
      if (filter !== "all" && profile.kind !== filter) return false;
      if (!needle) return true;
      return [profile.name, profile.model, profile.baseUrl, profile.adapter]
        .some((value) => String(value || "").toLowerCase().includes(needle));
    });
  }, [filter, profiles, query]);

  function commit(next: ModelSnapshot) {
    setSnapshot(next);
    onSnapshot?.(next);
  }

  async function refresh() {
    setBusy("refresh");
    setError("");
    try {
      commit(await window.loom.listModels<ModelSnapshot>());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy("");
    }
  }

  async function activate(profile: ModelProfile) {
    if (running || busy || profile.selection === current?.selection) return;
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
    setBusy(`test:${profile.selection}`);
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
    setForm({ ...EMPTY_FORM });
    setFormMode("add");
    setError("");
  }

  function openEdit(profile: ModelProfile) {
    if (profile.kind !== "saved" || profile.selection === current?.selection) return;
    setForm({
      selection: profile.selection,
      name: profile.name,
      adapter: profile.adapter === "openai" ? "openai" : "openai-compatible",
      baseUrl: profile.baseUrl,
      model: profile.model,
      apiKey: "",
      vision: profile.vision !== false,
    });
    setFormMode("edit");
    setError("");
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
    if (!value || running || busy) return;
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
          <span className="settings-eyebrow">{c.eyebrow}</span>
          <h1>{c.title}</h1>
          <p>{c.subtitle}</p>
        </div>
        <div className="models-heading-actions">
          <button type="button" className="mature-action-button" onClick={() => void refresh()} disabled={Boolean(busy)}>
            <RefreshCw size={14} className={busy === "refresh" ? "model-spin" : ""} />{c.refresh}
          </button>
          <button type="button" className="models-primary-action" onClick={openAdd} disabled={running || Boolean(busy)}>
            <Plus size={14} />{c.add}
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
                <span className="models-ready-badge"><i />{c.ready}</span>
              </div>
              <strong>{current?.name || currentModel}</strong>
              <span>{adapterLabel(current?.provider || current?.adapter || "openai-compatible")}</span>
            </div>
            {current ? (
              <button type="button" className="models-test-button" onClick={() => void test(current)} disabled={busy === `test:${current.selection}`}>
                <Wifi size={14} />{busy === `test:${current.selection}` ? c.testing : c.test}
              </button>
            ) : null}
          </div>

          <div className="models-active-meta">
            <div><span>{c.modelId}</span><code>{currentModel}</code></div>
            <div><span>{c.endpoint}</span><code title={current?.baseUrl || ""}>{current?.baseUrl || "OpenAI default"}</code></div>
            <div><span>{c.provider}</span><strong>{adapterLabel(current?.provider || current?.adapter || "openai-compatible")}</strong></div>
            <div><span>{c.reasoning}</span><strong>{currentReasoning?.options.find((option) => option.value === currentReasoning.value)?.label || currentReasoning?.value || "—"}</strong></div>
          </div>

          <div className="models-capability-row">
            <span><Activity size={12} />{c.chat}</span>
            <span><Zap size={12} />{c.stream}</span>
            {(currentTest?.capabilities.vision ?? current?.vision !== false) ? <span><Eye size={12} />{c.capabilityVision}</span> : null}
            {(currentTest?.capabilities.reasoning ?? Boolean(currentReasoning)) ? <span><Sparkles size={12} />{c.capabilityReasoning}</span> : null}
          </div>

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
        <div className="models-section-heading">
          <div><h2>{c.profiles}</h2><p>{c.profilesCaption}</p></div>
        </div>
        <div className="models-toolbar">
          <label className="models-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={c.search} /></label>
          <div className="models-filter" role="group" aria-label="Model profile filter">
            {(["all", "builtin", "saved"] as const).map((value) => (
              <button type="button" key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>
                {value === "all" ? c.all : value === "builtin" ? c.builtinFilter : c.savedFilter}
              </button>
            ))}
          </div>
        </div>

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
                  <div className="models-profile-title"><strong>{profile.name}</strong><span>{adapterLabel(profile.adapter)}</span></div>
                  <span className={`models-kind-badge ${profile.kind}`}>{profile.kind === "saved" ? c.saved : c.builtin}</span>
                </div>
                <div className="models-profile-data">
                  <div><span>{c.modelId}</span><code>{profile.model}</code></div>
                  <div><span>{c.endpoint}</span><code title={profile.baseUrl}>{endpointLabel(profile.baseUrl)}</code></div>
                </div>
                <div className="models-capability-row compact">
                  <span>{c.chat}</span><span>{c.stream}</span>
                  {(testResult?.capabilities.vision ?? profile.vision !== false) ? <span>{c.capabilityVision}</span> : null}
                  {(testResult?.capabilities.reasoning ?? Boolean(profile.reasoning)) ? <span>{c.capabilityReasoning}</span> : null}
                </div>
                {(testResult || testError) ? (
                  <div className={`models-card-diagnostic ${testError ? "error" : "success"}`}>
                    <i />
                    <span>{testError || `${testResult?.latencyMs ?? "—"}ms · ${testResult?.discoveredModels ?? 0} ${c.discovered}`}</span>
                  </div>
                ) : null}
                <div className="models-profile-actions">
                  <button type="button" className={active ? "active" : ""} disabled={running || Boolean(busy) || active} onClick={() => void activate(profile)}>
                    {isSwitching ? <RefreshCw size={13} className="model-spin" /> : active ? <Check size={13} /> : <ChevronRight size={13} />}
                    {active ? c.current : c.setActive}
                  </button>
                  <button type="button" disabled={isTesting} onClick={() => void test(profile)}><Wifi size={13} />{isTesting ? c.testing : c.test}</button>
                  {profile.kind === "saved" ? (
                    <>
                      <button type="button" disabled={running || Boolean(busy) || active} title={active ? c.activeEdit : c.edit} onClick={() => openEdit(profile)}><Pencil size={13} />{c.edit}</button>
                      <button type="button" className={confirmDelete === profile.selection ? "danger confirm" : "danger"} disabled={running || Boolean(busy)} onClick={() => void remove(profile)}>
                        {isDeleting ? <RefreshCw size={13} className="model-spin" /> : <Trash2 size={13} />}
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
      </section>

      <section className="models-section">
        <div className="models-section-heading"><div><h2>{c.recent}</h2><p>{c.recentCaption}</p></div></div>
        <div className="models-recent-panel">
          <div className="models-other-model-row">
            <input value={otherModel} onChange={(event) => setOtherModel(event.target.value)} placeholder="gpt-5 / claude / qwen / ..." disabled={running || Boolean(busy)} />
            <button type="button" disabled={!otherModel.trim() || running || Boolean(busy)} onClick={() => void switchOtherModel()}><RefreshCw size={13} />{c.switch}</button>
          </div>
          {recent.length ? <div className="models-recent-chips">{recent.map((model) => <button type="button" key={model} disabled={running || Boolean(busy)} onClick={() => void switchOtherModel(model)}>{model}</button>)}</div> : <span className="models-recent-empty">{c.noRecent}</span>}
        </div>
      </section>

      {formMode ? (
        <div className="models-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setFormMode(null); }}>
          <section className="models-modal" role="dialog" aria-modal="true" aria-label={formMode === "add" ? c.addTitle : c.editTitle}>
            <div className="models-modal-head">
              <div><span className="models-modal-icon">{formMode === "add" ? <Plus size={17} /> : <Pencil size={17} />}</span><div><strong>{formMode === "add" ? c.addTitle : c.editTitle}</strong><span>{adapterLabel(form.adapter)}</span></div></div>
              <button type="button" onClick={() => setFormMode(null)}><X size={16} /></button>
            </div>
            <div className="models-form-grid">
              <label className="wide"><span>{c.name}</span><input autoFocus value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
              <label><span>{c.apiType}</span><select value={form.adapter} onChange={(event) => setForm({ ...form, adapter: event.target.value as ModelFormState["adapter"] })}><option value="openai-compatible">OpenAI Compatible</option><option value="openai">OpenAI</option></select></label>
              <label><span>{c.modelId}</span><input value={form.model} onChange={(event) => setForm({ ...form, model: event.target.value })} /></label>
              <label className="wide"><span>{c.baseUrl}</span><input value={form.baseUrl} disabled={form.adapter === "openai"} placeholder={form.adapter === "openai" ? "OpenAI default" : "https://api.example.com/v1"} onChange={(event) => setForm({ ...form, baseUrl: event.target.value })} /></label>
              <label className="wide"><span>{formMode === "edit" ? c.apiKeyEdit : c.apiKey}</span><div className="models-secret-field"><KeyRound size={14} /><input type="password" value={form.apiKey} onChange={(event) => setForm({ ...form, apiKey: event.target.value })} /></div></label>
              <div className="models-form-switch wide"><div><Eye size={15} /><span><strong>{c.vision}</strong><small>{c.visionDesc}</small></span></div><button type="button" role="switch" aria-checked={form.vision} className={form.vision ? "on" : ""} onClick={() => setForm({ ...form, vision: !form.vision })}><i /></button></div>
            </div>
            <div className="models-key-note"><KeyRound size={13} /><span>{c.keyNote}</span></div>
            {error ? <div className="models-error-banner compact"><CircleAlert size={14} /><span>{error}</span></div> : null}
            <div className="models-modal-actions"><button type="button" onClick={() => setFormMode(null)}>{c.cancel}</button><button type="button" className="primary" disabled={Boolean(busy) || running} onClick={() => void submitForm()}>{busy.startsWith(formMode === "edit" ? "edit:" : "add") ? <RefreshCw size={13} className="model-spin" /> : formMode === "edit" ? <Check size={13} /> : <Plus size={13} />}{formMode === "edit" ? c.saveChanges : c.saveUse}</button></div>
          </section>
        </div>
      ) : null}
    </div>
  );
}
