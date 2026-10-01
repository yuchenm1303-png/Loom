/**
 * Web Search settings page.
 *
 * The API key is write-only and provider changes are refused while a turn is
 * active, so the panel keeps an explicit provider draft and explicit key save.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Check,
  CircleAlert,
  Eye,
  EyeOff,
  Globe2,
  KeyRound,
  Save,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useI18n } from "../i18n";
import "./settings-websearch.css";

type NoticeTone = "success" | "error";

type WebSearchStatus = {
  enabled?: boolean;
  configured?: boolean;
  provider?: string;
  choice?: string;
  keySource?: string;
  state?: string;
  reason?: string;
  keyRequired?: boolean;
  keyConfigured?: boolean;
};

type WebSearchTestResult = {
  ok?: boolean;
  provider?: string;
  latencyMs?: number;
  resultCount?: number;
  state?: string;
  error?: string;
};

type WebSearchConfigureResult = {
  status?: WebSearchStatus;
  settings?: unknown;
};

const PROVIDER_ORDER = ["auto", "loom", "tavily", "brave", "duckduckgo", "off"] as const;
const KEYED_PROVIDERS = new Set(["tavily", "brave"]);

function isKeyed(choice: string): boolean {
  return KEYED_PROVIDERS.has(String(choice || "").toLowerCase());
}

function pascal(value: string): string {
  return String(value || "")
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
}

function stateTone(state: string): string {
  if (state === "ready") return "ready";
  if (state === "error") return "error";
  return "off";
}

function message(cause: unknown): string {
  if (cause instanceof Error && cause.message) return cause.message;
  const text = String(cause ?? "").trim();
  return text || "Unknown error";
}

function providerGlyph(provider: string): string {
  if (provider === "auto") return "A";
  if (provider === "loom") return "L";
  if (provider === "tavily") return "T";
  if (provider === "brave") return "B";
  if (provider === "duckduckgo") return "D";
  return "×";
}

export function SettingsWebSearchPanel({
  running,
  onNotice,
}: {
  running: boolean;
  onNotice(tone: NoticeTone, text: string): void;
}) {
  const { t } = useI18n();

  const [status, setStatus] = useState<WebSearchStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [choiceDraft, setChoiceDraft] = useState("auto");
  const [savingChoice, setSavingChoice] = useState(false);
  const [keyDraft, setKeyDraft] = useState("");
  const [keyVisible, setKeyVisible] = useState(false);
  const [savingKey, setSavingKey] = useState(false);
  const [removingKey, setRemovingKey] = useState(false);
  const [testQuery, setTestQuery] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<WebSearchTestResult | null>(null);

  const applyStatus = useCallback((next: WebSearchStatus | undefined) => {
    if (!next) return;
    setStatus(next);
    setChoiceDraft(String(next.choice || "auto"));
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const result = await window.loom.call<{ status?: WebSearchStatus }>("web_search/status", {});
      applyStatus(result?.status);
      setLoadError("");
    } catch (cause) {
      setLoadError(message(cause));
    } finally {
      setLoading(false);
    }
  }, [applyStatus]);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const choice = String(status?.choice || "auto");
  const draftIsKeyed = isKeyed(choiceDraft);
  const choiceChanged = choiceDraft !== choice;
  const state = String(status?.state || "not_configured");
  const busy = Boolean(savingChoice || savingKey || removingKey);

  const saveChoice = async () => {
    setSavingChoice(true);
    try {
      const result = await window.loom.call<WebSearchConfigureResult>("web_search/configure", {
        provider: choiceDraft,
      });
      applyStatus(result?.status);
      onNotice("success", t("settings.websearch.toast.choiceSaved"));
    } catch (cause) {
      onNotice("error", message(cause));
    } finally {
      setSavingChoice(false);
    }
  };

  const saveKey = async () => {
    const secret = keyDraft.trim();
    if (!secret) return;
    setSavingKey(true);
    try {
      const result = await window.loom.call<WebSearchConfigureResult>("web_search/configure", {
        provider: choiceDraft,
        apiKey: secret,
      });
      applyStatus(result?.status);
      setKeyDraft("");
      setKeyVisible(false);
      onNotice("success", t("settings.websearch.toast.keySaved"));
    } catch (cause) {
      onNotice("error", message(cause));
    } finally {
      setSavingKey(false);
    }
  };

  const removeKey = async () => {
    setRemovingKey(true);
    try {
      const result = await window.loom.call<WebSearchConfigureResult>("web_search/configure", {
        provider: choiceDraft,
        clearKey: true,
      });
      applyStatus(result?.status);
      setKeyDraft("");
      onNotice("success", t("settings.websearch.toast.keyRemoved"));
    } catch (cause) {
      onNotice("error", message(cause));
    } finally {
      setRemovingKey(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    try {
      const result = await window.loom.call<WebSearchTestResult>("web_search/test", {
        query: testQuery.trim(),
      });
      setTestResult(result);
      applyStatus((result as { status?: WebSearchStatus } | undefined)?.status);
      onNotice(
        result?.ok ? "success" : "error",
        result?.ok
          ? t("settings.websearch.toast.testSuccess")
          : t("settings.websearch.toast.testFailed"),
      );
    } catch (cause) {
      setTestResult({ ok: false, error: message(cause) });
      onNotice("error", message(cause));
    } finally {
      setTesting(false);
    }
  };

  const stateLabel = t(`settings.websearch.state${pascal(state)}`);
  const keySourceLabel =
    status?.keySource === "account"
      ? t("settings.websearch.keySourceAccount")
      : status?.keySource === "keyring"
        ? t("settings.websearch.keySourceKeyring")
        : status?.keySource === "environment"
          ? t("settings.websearch.keySourceEnvironment")
          : t("settings.websearch.keySourceNone");
  const activeProvider = loading ? "…" : String(status?.provider || "disabled");
  const activeProviderDescription = status?.reason
    ? String(status.reason)
    : t(`settings.websearch.providerOption.${choice}Desc`);

  return (
    <div className="websearch-settings-page">
      <div className="settings-page-heading settings-heading-with-switch websearch-page-heading">
        <div>
          <span className="settings-eyebrow">{t("settings.websearch.eyebrow")}</span>
          <h1>{t("settings.websearch.title")}</h1>
          <p>{t("settings.websearch.description")}</p>
        </div>
        <div className="settings-master-switch">
          <span className={`settings-status-pill ${stateTone(state)}`}>
            <span className="settings-status-dot" />
            {stateLabel}
          </span>
        </div>
      </div>

      {running ? (
        <div className="settings-callout warning websearch-callout">
          <CircleAlert size={16} />
          <div>
            <strong>{t("settings.turnActive")}</strong>
            <span>Finish or stop the current turn before changing web search.</span>
          </div>
        </div>
      ) : null}

      {loadError ? (
        <div className="settings-callout warning websearch-callout">
          <CircleAlert size={16} />
          <div>
            <strong>{t("settings.websearch.stateError")}</strong>
            <span>{loadError}</span>
          </div>
        </div>
      ) : null}

      <section className="websearch-summary-card" aria-label={t("settings.websearch.runtimeStatus")}>
        <div className="websearch-summary-lead">
          <span className="websearch-summary-icon" aria-hidden="true"><Globe2 size={20} strokeWidth={1.7} /></span>
          <div>
            <span className="websearch-summary-kicker">{t("settings.websearch.runtimeStatus")}</span>
            <strong>{activeProvider}</strong>
            <p>{activeProviderDescription}</p>
          </div>
        </div>
        <div className="websearch-summary-grid">
          <div className="websearch-summary-stat">
            <Search size={15} />
            <span>{t("settings.websearch.choice")}</span>
            <strong>{choice}</strong>
          </div>
          <div className="websearch-summary-stat">
            <ShieldCheck size={15} />
            <span>{t("settings.websearch.keySource")}</span>
            <strong>{keySourceLabel}</strong>
          </div>
          <div className="websearch-summary-stat">
            <Activity size={15} />
            <span>{t("settings.websearch.state")}</span>
            <strong>{stateLabel}</strong>
          </div>
          <div className="websearch-summary-stat">
            <Sparkles size={15} />
            <span>{t("settings.websearch.active")}</span>
            <strong>{t(status?.enabled ? "settings.status.on" : "settings.status.off")}</strong>
          </div>
        </div>
      </section>

      <section className="settings-section websearch-provider-section">
        <div className="settings-section-heading websearch-section-heading">
          <div>
            <h2>{t("settings.websearch.provider")}</h2>
            <p>{t("settings.websearch.description")}</p>
          </div>
          <span className="websearch-section-state">{t("settings.models.current")}: {choice}</span>
        </div>
        <div className="settings-card websearch-provider-card">
          <div className="websearch-choice-list" role="radiogroup" aria-label={t("settings.websearch.provider")}>
            {PROVIDER_ORDER.map((value) => {
              const selected = choiceDraft === value;
              const current = value === choice;
              return (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  disabled={busy || running}
                  data-provider={value}
                  className={`websearch-choice ${selected ? "selected" : ""}`}
                  onClick={() => setChoiceDraft(value)}
                >
                  <span className="websearch-provider-glyph" aria-hidden="true">{providerGlyph(value)}</span>
                  <span className="websearch-choice-copy">
                    <span className="websearch-choice-title-row">
                      <strong>{t(`settings.websearch.providerOption.${value}`)}</strong>
                      {current ? <span className="websearch-choice-current">{t("settings.models.current")}</span> : null}
                    </span>
                    <span>{t(`settings.websearch.providerOption.${value}Desc`)}</span>
                  </span>
                  <span className="websearch-choice-mark" aria-hidden="true">
                    {selected ? <Check size={12} strokeWidth={2.8} /> : null}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="websearch-choice-actions">
            <span className="websearch-hint">
              {isKeyed(choiceDraft) ? t("settings.websearch.apiKey") : t("settings.websearch.noKeyNeeded")}
            </span>
            <button
              className="mature-action-button websearch-save-choice"
              type="button"
              disabled={!choiceChanged || busy || running}
              onClick={() => void saveChoice()}
            >
              <Save size={14} />
              {savingChoice ? t("settings.websearch.savingChoice") : t("settings.websearch.saveChoice")}
            </button>
          </div>
        </div>
      </section>

      <div className="websearch-tools-grid">
        <section className="settings-section websearch-compact-section">
          <div className="settings-section-heading websearch-section-heading">
            <div>
              <h2>{t("settings.websearch.apiKey")}</h2>
              <p>{keySourceLabel}</p>
            </div>
            <span className={`websearch-key-status ${status?.keyConfigured ? "ok" : ""}`}>
              {status?.keyConfigured
                ? t("settings.websearch.keyStatusConfigured")
                : t("settings.websearch.keyStatusNotConfigured")}
            </span>
          </div>
          <div className="settings-card websearch-key-card">
            <div className="websearch-key-row">
              <span className="websearch-key-icon" aria-hidden="true">
                <KeyRound size={16} strokeWidth={1.8} />
              </span>
              <input
                className="mature-input"
                type={keyVisible ? "text" : "password"}
                value={keyDraft}
                autoComplete="off"
                spellCheck={false}
                disabled={!draftIsKeyed || busy || running}
                placeholder={
                  status?.keyConfigured
                    ? t("settings.websearch.apiKeyPlaceholderConfigured")
                    : t("settings.websearch.apiKeyPlaceholder")
                }
                aria-label={t("settings.websearch.apiKey")}
                onChange={(event) => setKeyDraft(event.target.value)}
              />
              <button
                className="mature-action-button secondary websearch-key-toggle"
                type="button"
                disabled={!keyDraft}
                onClick={() => setKeyVisible((value) => !value)}
                aria-label={keyVisible ? t("settings.websearch.apiKeyHide") : t("settings.websearch.apiKeyShow")}
              >
                {keyVisible ? <EyeOff size={14} /> : <Eye size={14} />}
                <span>{keyVisible ? t("settings.websearch.apiKeyHide") : t("settings.websearch.apiKeyShow")}</span>
              </button>
            </div>
            <div className="websearch-key-actions">
              <span className="websearch-hint">
                {draftIsKeyed ? t(`settings.websearch.providerOption.${choiceDraft}Desc`) : t("settings.websearch.noKeyNeeded")}
              </span>
              <div className="websearch-action-cluster">
                <button
                  className="mature-action-button"
                  type="button"
                  disabled={!keyDraft.trim() || !draftIsKeyed || busy || running}
                  onClick={() => void saveKey()}
                >
                  <Save size={14} />
                  {savingKey ? t("settings.websearch.apiKeySaving") : t("settings.websearch.apiKeySave")}
                </button>
                <button
                  className="mature-action-button secondary websearch-remove-key"
                  type="button"
                  disabled={!status?.keyConfigured || busy || running}
                  onClick={() => void removeKey()}
                >
                  <Trash2 size={14} />
                  {removingKey ? t("settings.websearch.apiKeyRemoving") : t("settings.websearch.apiKeyRemove")}
                </button>
              </div>
            </div>
          </div>
        </section>

        <section className="settings-section websearch-compact-section">
          <div className="settings-section-heading websearch-section-heading">
            <div>
              <h2>{t("settings.websearch.testSearch")}</h2>
              <p>{activeProvider}</p>
            </div>
          </div>
          <div className="settings-card websearch-test-card">
            <div className="websearch-test-row">
              <span className="websearch-key-icon" aria-hidden="true">
                <Globe2 size={16} strokeWidth={1.8} />
              </span>
              <input
                className="mature-input"
                value={testQuery}
                disabled={testing || running}
                placeholder={t("settings.websearch.testPlaceholder")}
                aria-label={t("settings.websearch.testSearch")}
                onChange={(event) => setTestQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !testing && !running) void runTest();
                }}
              />
              <button
                className="mature-action-button websearch-test-button"
                type="button"
                disabled={testing || running}
                onClick={() => void runTest()}
              >
                <Activity size={14} />
                {testing ? t("settings.websearch.testing") : t("settings.websearch.testSearch")}
              </button>
            </div>
            {testResult ? (
              <div className={`websearch-test-result ${testResult.ok ? "ok" : "bad"}`}>
                <strong>
                  {testResult.ok ? t("settings.websearch.testSuccess") : t("settings.websearch.testFailed")}
                </strong>
                <span>
                  {t("settings.websearch.provider")}: {String(testResult.provider || "—")} ·{" "}
                  {t("settings.websearch.testResults")}: {Number(testResult.resultCount ?? 0)} ·{" "}
                  {t("settings.websearch.testLatency")}: {Number(testResult.latencyMs ?? 0)} ms
                </span>
                {testResult.error ? <code>{String(testResult.error)}</code> : null}
              </div>
            ) : (
              <div className="websearch-test-empty">
                <Search size={16} />
                <span>{t("settings.websearch.testPlaceholder")}</span>
              </div>
            )}
            {isKeyed(choice) && !status?.keyConfigured ? (
              <p className="websearch-hint">{t("settings.websearch.testNoKey")}</p>
            ) : null}
          </div>
        </section>
      </div>

      <div className="settings-callout websearch-callout websearch-info-callout">
        <CircleAlert size={16} />
        <div>
          <strong>{t("settings.websearch.safetyTitle")}</strong>
          <span>{t("settings.websearch.safetyBody")}</span>
        </div>
      </div>
    </div>
  );
}
