/**
 * Web Search settings page.
 *
 * Talks to three App Server methods, all of which are already implemented on the
 * Python side:
 *
 *   web_search/status     -> { status }
 *   web_search/configure  -> { status, settings, runtime }   (refused mid-turn)
 *   web_search/test       -> { ok, provider, latencyMs, resultCount, state, error, status }
 *
 * Two rules from the backend shape this UI:
 *
 * 1. The API key is write-only. `status` reports booleans (`keyConfigured`,
 *    `keyRequired`) and never the key, its prefix, or its length, so there is
 *    nothing to prefill — the field starts blank and blank means "keep what is
 *    stored". That is why saving is an explicit action rather than an on-change
 *    write: a keystroke-level autosave would push a half-typed key into the OS
 *    credential store.
 *
 * 2. Changing provider re-registers the runtime's tool family, so the backend
 *    refuses while a turn is active. We surface that as a callout instead of
 *    letting the user hit an error.
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

/** Display order. Keyed providers sit above the keyless one so the key field,
 *  which only applies to them, is visible while the user is choosing. */
const PROVIDER_ORDER = ["auto", "tavily", "brave", "duckduckgo", "off"] as const;

/** Mirrors `_KEYED_PROVIDERS` in app/web_search_settings.py. Kept in sync by
 *  hand: the frontend uses it only to decide whether to show the key field, and
 *  the backend re-validates every write regardless. */
const KEYED_PROVIDERS = new Set(["tavily", "brave"]);

function isKeyed(choice: string): boolean {
  return KEYED_PROVIDERS.has(String(choice || "").toLowerCase());
}

/** `not_configured` -> `NotConfigured`, so the state can index into the
 *  `settings.websearch.state*` keys without a hand-written lookup table. */
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

  // The radio group is a draft: nothing is persisted until "Save choice".
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
      // Send the provider being saved for, not the stored choice: the user may
      // have picked Tavily in the radio group without saving the choice yet, and
      // storing the key against the wrong provider would silently never be used.
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
    status?.keySource === "keyring"
      ? t("settings.websearch.keySourceKeyring")
      : status?.keySource === "environment"
        ? t("settings.websearch.keySourceEnvironment")
        : t("settings.websearch.keySourceNone");

  return (
    <div className="websearch-settings-page">
      <div className="settings-page-heading settings-heading-with-switch">
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
            {/* The websearch key set has no mid-turn sentence, so this one is
                written out rather than borrowed from an unrelated key. Add
                `settings.websearch.turnActiveBody` and swap it in when the
                strings next get a pass. */}
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

      <section className="settings-section">
        <div className="settings-section-heading">
          <h2>{t("settings.websearch.runtimeStatus")}</h2>
        </div>
        <div className="settings-card settings-detail-list">
          <div className="settings-detail-row">
            <div>
              <strong>{t("settings.websearch.provider")}</strong>
            </div>
            <code title={String(status?.provider || "")}>
              {loading ? "…" : String(status?.provider || "disabled")}
            </code>
          </div>
          <div className="settings-detail-row">
            <div>
              <strong>{t("settings.websearch.choice")}</strong>
            </div>
            <code>{choice}</code>
          </div>
          <div className="settings-detail-row">
            <div>
              <strong>{t("settings.websearch.keySource")}</strong>
            </div>
            <code>{keySourceLabel}</code>
          </div>
          <div className="settings-detail-row">
            <div>
              <strong>{t("settings.websearch.state")}</strong>
            </div>
            <code>{stateLabel}</code>
          </div>
          <div className="settings-detail-row">
            <div>
              <strong>{t("settings.websearch.active")}</strong>
            </div>
            {/* Localised on/off rather than a hardcoded "yes"/"no": this row is
                visible in the Chinese UI too. */}
            <code>{t(status?.enabled ? "settings.status.on" : "settings.status.off")}</code>
          </div>
          {status?.reason ? (
            <div className="settings-detail-row">
              <div>
                <strong>{t("settings.websearch.reason")}</strong>
              </div>
              <code title={String(status.reason)}>{String(status.reason)}</code>
            </div>
          ) : null}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-heading">
          <h2>{t("settings.websearch.provider")}</h2>
        </div>
        <div className="settings-card websearch-choice-list" role="radiogroup" aria-label={t("settings.websearch.provider")}>
          {PROVIDER_ORDER.map((value) => {
            const selected = choiceDraft === value;
            return (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={selected}
                disabled={busy || running}
                className={`websearch-choice ${selected ? "selected" : ""}`}
                onClick={() => setChoiceDraft(value)}
              >
                <span className="websearch-choice-mark" aria-hidden="true">
                  {selected ? <Check size={13} strokeWidth={2.6} /> : null}
                </span>
                <span className="websearch-choice-copy">
                  <strong>{t(`settings.websearch.providerOption.${value}`)}</strong>
                  <span>{t(`settings.websearch.providerOption.${value}Desc`)}</span>
                </span>
                {value === choice ? (
                  /* `settings.models.current` ("Current" / "当前") is the badge
                     the models panel already uses for "this one is selected".
                     `settings.websearch.active` is the runtime-status row label
                     ("Active" / "是否生效"), which reads as a question when it
                     is used as a badge. */
                  <span className="websearch-choice-current">{t("settings.models.current")}</span>
                ) : null}
              </button>
            );
          })}
        </div>
        <div className="settings-inline-actions websearch-choice-actions">
          <button
            className="mature-action-button"
            type="button"
            disabled={!choiceChanged || busy || running}
            onClick={() => void saveChoice()}
          >
            {savingChoice ? t("settings.websearch.savingChoice") : t("settings.websearch.saveChoice")}
          </button>
          {!isKeyed(choiceDraft) ? (
            <span className="websearch-hint">{t("settings.websearch.noKeyNeeded")}</span>
          ) : null}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-heading">
          <h2>{t("settings.websearch.apiKey")}</h2>
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
              {keyVisible ? t("settings.websearch.apiKeyHide") : t("settings.websearch.apiKeyShow")}
            </button>
          </div>
          <div className="settings-inline-actions websearch-key-actions">
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
              className="mature-action-button secondary"
              type="button"
              disabled={!status?.keyConfigured || busy || running}
              onClick={() => void removeKey()}
            >
              <Trash2 size={14} />
              {removingKey ? t("settings.websearch.apiKeyRemoving") : t("settings.websearch.apiKeyRemove")}
            </button>
            <span className={`websearch-key-status ${status?.keyConfigured ? "ok" : ""}`}>
              {status?.keyConfigured
                ? t("settings.websearch.keyStatusConfigured")
                : t("settings.websearch.keyStatusNotConfigured")}
            </span>
          </div>
          {!draftIsKeyed ? (
            <p className="websearch-hint">{t("settings.websearch.noKeyNeeded")}</p>
          ) : null}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-heading">
          <h2>{t("settings.websearch.testSearch")}</h2>
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
            />
            <button
              className="mature-action-button"
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
          ) : null}
          {isKeyed(choice) && !status?.keyConfigured ? (
            <p className="websearch-hint">{t("settings.websearch.testNoKey")}</p>
          ) : null}
        </div>
      </section>

      <div className="settings-callout websearch-callout">
        <CircleAlert size={16} />
        <div>
          <strong>{t("settings.websearch.safetyTitle")}</strong>
          <span>{t("settings.websearch.safetyBody")}</span>
        </div>
      </div>
    </div>
  );
}
