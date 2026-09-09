import {
  ArrowUp,
  Check,
  ChevronDown,
  Cpu,
  Eye,
  FolderCog,
  KeyRound,
  Paperclip,
  ShieldCheck,
  Sparkles,
  Square,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import "./composer.css";

interface ComposerProps {
  disabled?: boolean;
  running?: boolean;
  model?: string;
  models?: string[];
  permissionMode?: string;
  permissionModes?: string[];
  onPermissionModeChange?(mode: string): Promise<void> | void;
  onModelChange?(model: string): Promise<void> | void;
  onSend(input: string): Promise<void> | void;
  onInterrupt(): Promise<void> | void;
}

type OpenPanel = "permission" | "model" | null;

type PermissionPresentation = {
  label: string;
  description: string;
  badge: string;
  tone: "safe" | "recommended" | "flexible" | "danger";
};

const PERMISSION_PRESENTATION: Record<string, PermissionPresentation> = {
  "read-only": {
    label: "Read only",
    description: "Inspect files, context, and tools without making workspace changes.",
    badge: "Safest",
    tone: "safe",
  },
  approval: {
    label: "Approval",
    description: "Run normal work directly and ask before sensitive or privileged actions.",
    badge: "Recommended",
    tone: "recommended",
  },
  workspace: {
    label: "Workspace",
    description: "Allow routine edits and commands inside the active workspace with fewer prompts.",
    badge: "Flexible",
    tone: "flexible",
  },
  "full-access": {
    label: "Full access",
    description: "Allow Loom to act with the broadest available permissions on this machine.",
    badge: "Highest access",
    tone: "danger",
  },
};

function titleCase(value: string): string {
  return value
    .replace(/[-_]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function permissionPresentation(mode: string): PermissionPresentation {
  return PERMISSION_PRESENTATION[mode] ?? {
    label: titleCase(mode),
    description: "Permission policy exposed by the current Loom runtime.",
    badge: "Runtime",
    tone: "flexible",
  };
}

function PermissionIcon({ mode }: { mode: string }) {
  if (mode === "read-only") return <Eye size={15} />;
  if (mode === "workspace") return <FolderCog size={15} />;
  if (mode === "full-access") return <KeyRound size={15} />;
  return <ShieldCheck size={15} />;
}

export function Composer({
  disabled,
  running,
  model,
  models,
  permissionMode,
  permissionModes,
  onPermissionModeChange,
  onModelChange,
  onSend,
  onInterrupt,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const [openPanel, setOpenPanel] = useState<OpenPanel>(null);
  const [pendingSelection, setPendingSelection] = useState("");
  const [panelError, setPanelError] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const composerRootRef = useRef<HTMLDivElement | null>(null);

  const availablePermissionModes = useMemo(() => {
    const values = permissionModes?.length ? permissionModes : ["read-only", "approval", "workspace", "full-access"];
    return Array.from(new Set(values.filter(Boolean)));
  }, [permissionModes]);

  const availableModels = useMemo(() => {
    const values = [...(models ?? []), model ?? ""].filter(Boolean);
    return Array.from(new Set(values));
  }, [model, models]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  }, [value]);

  useEffect(() => {
    function handlePointerDown(event: PointerEvent) {
      if (!composerRootRef.current?.contains(event.target as Node)) {
        setOpenPanel(null);
        setPanelError("");
      }
    }

    function handleEscape(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setOpenPanel(null);
        setPanelError("");
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, []);

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const input = value.trim();
    if (!input || disabled || running) return;
    setValue("");
    setOpenPanel(null);
    await onSend(input);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }

  function togglePanel(panel: Exclude<OpenPanel, null>) {
    setPanelError("");
    setOpenPanel((current) => (current === panel ? null : panel));
  }

  async function choosePermission(mode: string) {
    if (mode === permissionMode) {
      setOpenPanel(null);
      return;
    }
    if (!onPermissionModeChange || running) return;

    setPendingSelection(mode);
    setPanelError("");
    try {
      await onPermissionModeChange(mode);
      setOpenPanel(null);
    } catch (cause) {
      setPanelError(cause instanceof Error ? cause.message : "Could not update permission profile.");
    } finally {
      setPendingSelection("");
    }
  }

  async function chooseModel(nextModel: string) {
    if (nextModel === model) {
      setOpenPanel(null);
      return;
    }
    if (!onModelChange || running) return;

    setPendingSelection(nextModel);
    setPanelError("");
    try {
      await onModelChange(nextModel);
      setOpenPanel(null);
    } catch (cause) {
      setPanelError(cause instanceof Error ? cause.message : "Could not switch models.");
    } finally {
      setPendingSelection("");
    }
  }

  const currentPermission = permissionPresentation(permissionMode || "approval");

  return (
    <div className="composer-wrap" ref={composerRootRef}>
      <form className={`composer ${focused ? "is-focused" : ""} ${running ? "is-running" : ""} ${openPanel ? "has-panel" : ""}`} onSubmit={submit}>
        <span className="composer-glow" aria-hidden="true" />
        <div className="composer-input-row">
          <span className="composer-spark" aria-hidden="true"><Sparkles size={15} /></span>
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={onKeyDown}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder={disabled ? "Open a thread to start" : running ? "Loom is working…" : "Ask Loom to inspect, build, debug, or automate…"}
            disabled={disabled || running}
            rows={1}
          />
        </div>

        <div className="composer-toolbar">
          <div className="composer-left">
            <button type="button" className="composer-tool" title="Attach files" disabled>
              <Paperclip size={15} />
              <span>Attach</span>
            </button>
            <span className="composer-divider" />

            <div className="composer-control-anchor">
              <button
                type="button"
                className={`composer-chip permission-chip ${openPanel === "permission" ? "is-open" : ""}`}
                title="Permission profile"
                aria-haspopup="menu"
                aria-expanded={openPanel === "permission"}
                onClick={() => togglePanel("permission")}
              >
                <ShieldCheck size={13} />
                <span>{currentPermission.label}</span>
                <ChevronDown size={13} className="composer-chip-chevron" />
              </button>

              {openPanel === "permission" ? (
                <div className="composer-popover permission-popover" role="menu" aria-label="Permission profiles">
                  <div className="composer-popover-head">
                    <div className="composer-popover-heading">
                      <span className="composer-popover-icon permission"><ShieldCheck size={16} /></span>
                      <div>
                        <strong>Permission profile</strong>
                        <span>Choose how independently Loom can act in this thread.</span>
                      </div>
                    </div>
                    <span className="composer-popover-context">Thread</span>
                  </div>

                  <div className="composer-option-list">
                    {availablePermissionModes.map((mode) => {
                      const presentation = permissionPresentation(mode);
                      const active = mode === permissionMode;
                      const pending = pendingSelection === mode;
                      return (
                        <button
                          key={mode}
                          type="button"
                          className={`composer-option permission-option ${active ? "active" : ""} ${presentation.tone === "danger" ? "danger" : ""}`}
                          role="menuitemradio"
                          aria-checked={active}
                          disabled={Boolean(pendingSelection) || running || (!onPermissionModeChange && !active)}
                          onClick={() => void choosePermission(mode)}
                        >
                          <span className={`composer-option-icon ${presentation.tone}`}><PermissionIcon mode={mode} /></span>
                          <span className="composer-option-copy">
                            <span className="composer-option-title-row">
                              <strong>{presentation.label}</strong>
                              <em className={`permission-badge ${presentation.tone}`}>{presentation.badge}</em>
                            </span>
                            <span className="composer-option-description">{presentation.description}</span>
                          </span>
                          <span className={`composer-option-check ${pending ? "pending" : ""}`}>
                            {pending ? <span className="composer-mini-spinner" /> : active ? <Check size={13} /> : null}
                          </span>
                        </button>
                      );
                    })}
                  </div>

                  {panelError ? <div className="composer-popover-error">{panelError}</div> : null}
                  <div className="composer-popover-footnote">
                    Changes apply to the next turn. Active work must finish before the profile can change.
                  </div>
                </div>
              ) : null}
            </div>

            <div className="composer-control-anchor model-control-anchor">
              <button
                type="button"
                className={`composer-chip model-chip ${openPanel === "model" ? "is-open" : ""}`}
                title={model || "Model"}
                aria-haspopup="menu"
                aria-expanded={openPanel === "model"}
                onClick={() => togglePanel("model")}
              >
                <Cpu size={13} />
                <span>{model || "Model"}</span>
                <ChevronDown size={13} className="composer-chip-chevron" />
              </button>

              {openPanel === "model" ? (
                <div className="composer-popover model-popover" role="menu" aria-label="Models">
                  <div className="composer-popover-head">
                    <div className="composer-popover-heading">
                      <span className="composer-popover-icon model"><Cpu size={16} /></span>
                      <div>
                        <strong>Model</strong>
                        <span>Select the model used for new work in this runtime.</span>
                      </div>
                    </div>
                    <span className="composer-popover-context">Runtime</span>
                  </div>

                  <div className="composer-option-list model-option-list">
                    {(availableModels.length ? availableModels : [model || "Model"]).map((candidate) => {
                      const active = candidate === model;
                      const pending = pendingSelection === candidate;
                      const selectable = active || Boolean(onModelChange);
                      return (
                        <button
                          key={candidate}
                          type="button"
                          className={`composer-option model-option ${active ? "active" : ""}`}
                          role="menuitemradio"
                          aria-checked={active}
                          disabled={Boolean(pendingSelection) || running || !selectable}
                          onClick={() => void chooseModel(candidate)}
                        >
                          <span className="composer-option-icon model"><Cpu size={15} /></span>
                          <span className="composer-option-copy">
                            <span className="composer-option-title-row">
                              <strong>{candidate}</strong>
                              {active ? <em className="model-active-badge"><i /> Active</em> : null}
                            </span>
                            <span className="composer-option-description">
                              {active ? "Connected to the current Agent Runtime." : "Configured model available to this runtime."}
                            </span>
                          </span>
                          <span className={`composer-option-check ${pending ? "pending" : ""}`}>
                            {pending ? <span className="composer-mini-spinner" /> : active ? <Check size={13} /> : null}
                          </span>
                        </button>
                      );
                    })}
                  </div>

                  {availableModels.length <= 1 && !onModelChange ? (
                    <div className="composer-model-notice">
                      <span className="model-notice-dot" />
                      <span>One model is configured. Additional model profiles will appear here automatically when the runtime exposes them.</span>
                    </div>
                  ) : null}
                  {panelError ? <div className="composer-popover-error">{panelError}</div> : null}
                  <div className="composer-popover-footnote">
                    Provider credentials stay behind the App Server boundary and are never exposed to the renderer.
                  </div>
                </div>
              ) : null}
            </div>
          </div>

          <div className="composer-right">
            {!running ? <span className="composer-keycap">Enter ↵</span> : <span className="composer-running-label"><i /> Working</span>}
            {running ? (
              <button type="button" className="send-button stop" onClick={() => void onInterrupt()} title="Stop current turn" aria-label="Stop current turn">
                <Square size={12} fill="currentColor" />
              </button>
            ) : (
              <button type="submit" className="send-button" disabled={disabled || !value.trim()} title="Send" aria-label="Send message">
                <ArrowUp size={17} strokeWidth={2.2} />
              </button>
            )}
          </div>
        </div>
      </form>
      <div className="composer-hint">Loom can use your workspace and connected tools. Review sensitive actions before approving them.</div>
    </div>
  );
}
