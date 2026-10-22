import {
  Check,
  ChevronDown,
  Eye,
  FolderOpen,
  FolderCog,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  ModelLayers,
  Paperclip,
  Send,
  ShieldCheck,
  Sparkles,
  Square,
} from "./icons";
import { KeyboardEvent as ReactKeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import type { AddModelInput, Attachment, ModelSnapshot, StickerPreferences } from "../types/loom";
import { useI18n } from "../i18n";
import { recordSendOrigin } from "../sendFlight";
import { Crossfade } from "./Crossfade";
import { ModelPanel } from "./ModelPanel";
import { ModelUsageIndicator } from "./ModelUsageIndicator";
import { ComposerDraftTools } from "./ComposerDraftTools";
import { ComposerToolSelector } from "./ComposerToolSelector";
import { ComposerAttachmentStrip } from "./ComposerAttachmentStrip";
import {
  ComposerPanel,
  ComposerPanelHost,
  PanelFoot,
  PanelHead,
  useComposerPanel,
  useComposerPanelHost,
  useInheritedComposerPanelHost,
} from "./composerPanel";
import {
  MAX_COMPOSER_ATTACHMENTS,
  appendComposerAttachments,
  attachmentFromPath,
  releaseAttachmentPreview,
  resolveComposerFiles,
} from "./composerAttachments";
import { QuoteReplyBar, formatQuotedPrompt, useQuoteReply } from "./quoteReply";
import "./composer.css";
import "./composer-attachment-polish.css";
import "./composer-stability.css";
import "./composer-control-pills.css";
import "./model-input-notice.css";
import "./composer-compact.css";
import { imageAttachmentState } from "./modelInput";

interface ComposerProps {
  /** Share the same settings controls with the live guidance composer. */
  controlsOnly?: boolean;
  toolSelection?: string[] | null;
  workspace?: string;
  onOpenWorkspace?(): void;
  threadId?: string;
  draftValue?: string;
  onDraftValueChange?(value: string): void;
  disabled?: boolean;
  /** Loom is still launching: say so instead of asking for a conversation that cannot exist yet. */
  starting?: boolean;
  running?: boolean;
  paused?: boolean;
  onPausedChange?(paused: boolean): Promise<void> | void;
  model?: string;
  modelSnapshot?: ModelSnapshot | null;
  modelBusy?: boolean;
  accountAuthenticated?: boolean;
  accountUsageIdentity?: string;
  onOpenAccount?(): void;
  permissionMode?: string;
  permissionModes?: string[];
  stickerPreferences?: StickerPreferences | null;
  onPermissionModeChange?(mode: string): Promise<void> | void;
  onModelProfileChange?(selection: string): Promise<void> | void;
  onCustomModelChange?(model: string): Promise<void> | void;
  onConfigureModelProvider?(provider: string, apiKey: string): Promise<void> | void;
  onRefreshModels?(forceRefresh?: boolean): Promise<unknown> | void;
  onRefreshActiveThread?(): Promise<void> | void;
  onAddModel?(input: AddModelInput): Promise<void> | void;
  onDeleteModel?(selection: string): Promise<void> | void;
  onReasoningChange?(kind: string, value: string): Promise<void> | void;
  onStickerPreferencesChange?(preferences: StickerPreferences): Promise<void> | void;
  /** True when the bound model was declared able to read images. */
  imagesAllowed?: boolean;
  imageSupport?: boolean | null;
  onSend(input: string, attachments: { path: string; name: string }[], toolSelection?: string[] | null): Promise<void> | void;
  onInterrupt(): Promise<void> | void;
}

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

const PERMISSION_ZH: Record<string, Pick<PermissionPresentation, "label" | "description" | "badge">> = {
  "read-only": { label: "只读", description: "查看文件和上下文，不修改工作区。", badge: "只读访问" },
  approval: { label: "按需审批", description: "敏感操作执行前需要你的确认。", badge: "推荐" },
  workspace: { label: "工作区", description: "允许在当前工作区内编辑文件和执行常规命令。", badge: "工作区访问" },
  "full-access": { label: "完全访问", description: "允许 Loom 使用本机可用的最高权限。", badge: "最高权限" },
};
function permissionPresentation(mode: string, chinese = false): PermissionPresentation {
  const base = PERMISSION_PRESENTATION[mode];
  if (base) return chinese ? { ...base, ...PERMISSION_ZH[mode] } : base;
  return {
    label: titleCase(mode),
    description: "Permission policy exposed by the current Loom runtime.",
    badge: "Runtime",
    tone: "flexible",
  };
}

function PermissionIcon({ mode, toolbar = false }: { mode: string; toolbar?: boolean }) {
  const Icon = mode === "read-only" ? Eye : mode === "workspace" ? FolderCog : mode === "full-access" ? KeyRound : ShieldCheck;
  return <Icon size={16} strokeWidth={1.75}
    className={toolbar ? "composer-control-icon composer-permission-icon" : undefined}
    aria-hidden="true" focusable="false" />;
}


// The toolbar's own marks live in the icon module, so they move like every other glyph.
function ComposerControlIcon({ kind }: { kind: "attach" | "model" }) {
  const Icon = kind === "attach" ? Paperclip : ModelLayers;
  return <Icon className="composer-control-icon" data-control-icon={kind} size={16} strokeWidth={1.75} focusable="false" />;
}


export function Composer({
  controlsOnly = false,
  toolSelection: savedToolSelection,
  workspace,
  onOpenWorkspace,
  threadId,
  draftValue,
  onDraftValueChange,
  disabled,
  starting,
  running,
  model,
  modelSnapshot,
  modelBusy,
  accountAuthenticated = false,
  accountUsageIdentity = "",
  onOpenAccount,
  permissionMode,
  permissionModes,
  onPermissionModeChange,
  onModelProfileChange,
  onCustomModelChange,
  onConfigureModelProvider,
  onRefreshModels,
  onRefreshActiveThread,
  onAddModel,
  onDeleteModel,
  onReasoningChange,
  imagesAllowed = true,
  imageSupport,
  onSend,
  onInterrupt,
}: ComposerProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [localValue, setLocalValue] = useState("");
  const [toolSelection, setToolSelection] = useState<string[] | null>(savedToolSelection ?? null);
  const savedToolSelectionKey = JSON.stringify(savedToolSelection ?? null);
  useEffect(() => { setToolSelection(JSON.parse(savedToolSelectionKey)); }, [threadId, savedToolSelectionKey]);
  const value = draftValue ?? localValue;
  const setValue = onDraftValueChange ?? setLocalValue;
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const imageNotice = imageAttachmentState(attachments.some(item => item.isImage), imagesAllowed, imageSupport, zh);
  const [attachError, setAttachError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [focused, setFocused] = useState(false);
  const composerRootRef = useRef<HTMLDivElement | null>(null);
  // One host knows which of the composer's panels is open (composerPanel.tsx). The live-guidance composer lends its
  // own host to these shared controls, so its preview and these panels still close each other.
  const inheritedHost = useInheritedComposerPanelHost();
  const ownHost = useComposerPanelHost(composerRootRef);
  const panels = inheritedHost ?? ownHost;
  const permissionPanel = useComposerPanel("permission", panels);
  const modelPanel = useComposerPanel("model", panels);
  const [pendingSelection, setPendingSelection] = useState("");
  const [panelError, setPanelError] = useState("");
  const [modelError, setModelError] = useState("");
  const modelRequestVersion = useRef(0);

  const { close: closePanels } = panels;
  useEffect(() => {
    modelRequestVersion.current += 1;
    setModelError("");
    closePanels();
  }, [threadId, closePanels]);

  async function selectModel(action: (() => Promise<void> | void) | undefined) {
    const version = ++modelRequestVersion.current;
    setModelError("");
    try {
      if (!action) throw new Error("Model switching is unavailable.");
      await action();
    } catch (cause) {
      if (version === modelRequestVersion.current) {
        setModelError(cause instanceof Error ? cause.message : String(cause));
      }
      throw cause;
    }
  }
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const [quote, setQuote] = useQuoteReply();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!running) setStopping(false);
  }, [running]);

  async function handleInterrupt(): Promise<void> {
    if (stopping) return;
    setStopping(true);
    setStopError("");
    try {
      await onInterrupt();
    } catch (cause) {
      setStopping(false);
      setStopError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  const availablePermissionModes = useMemo(() => {
    const values = permissionModes?.length ? permissionModes : ["read-only", "approval", "workspace", "full-access"];
    return Array.from(new Set(values.filter(Boolean)));
  }, [permissionModes]);

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  }, [value]);

  useEffect(() => {
    if (!quote) return;
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, [quote]);

  useEffect(() => {
    if (controlsOnly) return;
    setQuote(null);
  }, [threadId, setQuote, controlsOnly]);

  // A panel that closes forgets its error.
  useEffect(() => {
    if (!permissionPanel.open) setPanelError("");
  }, [permissionPanel.open]);


  const addAttachments = (incoming: Attachment[]) => {
    setAttachments((current) => {
      const result = appendComposerAttachments(current, incoming);
      if (result.overflowed) setAttachError(`At most ${MAX_COMPOSER_ATTACHMENTS} attachments per message.`);
      return result.attachments;
    });
  };

  const onPaste = async (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = [...event.clipboardData.files];
    if (!files.length) return;
    // Intercepted so the image does not also land in the prompt as pasted
    // rich content that is silently dropped on send.
    event.preventDefault();
    setAttachError("");
    addAttachments(await resolveComposerFiles(files, setAttachError));
  };

  const onDrop = async (event: React.DragEvent) => {
    if (!event.dataTransfer.files.length) return;
    event.preventDefault();
    setDragging(false);
    setAttachError("");
    addAttachments(await resolveComposerFiles([...event.dataTransfer.files], setAttachError));
  };

  const pickAttachments = async () => {
    const picked = (await window.loom?.pickFiles?.()) ?? [];
    setAttachError("");
    addAttachments(picked.map((filePath) => attachmentFromPath(filePath)));
  };

  const removeAttachment = (id: string) => {
    setAttachments((current) => {
      const target = current.find((item) => item.id === id);
      releaseAttachmentPreview(target);
      return current.filter((item) => item.id !== id);
    });
  };

  async function submit() {
    if (imageNotice.blocked) return;
    const typedInput = value.trim();
    const input = formatQuotedPrompt(quote, typedInput);
    const sendable = attachments;
    // A quote or attachment can carry context on its own; a truly empty composer cannot.
    if ((!typedInput && !quote && !sendable.length) || disabled || running || submittingRef.current) return;
    if (!accountAuthenticated) {
      onOpenAccount?.();
      return;
    }
    submittingRef.current = true;
    setSubmitting(true);
    panels.close();
    try {
      // Where the text is drawn now: the message flies from here once the transcript has its entry (sendFlight.ts).
      recordSendOrigin(textareaRef.current);
      const request = onSend(input, sendable.map((item) => ({ path: item.path, name: item.name })), toolSelection);
      setValue("");
      await request;
      setQuote(null);
      setAttachments([]);
      setAttachError("");
    } catch {
      // The Composer owner retains the error and draft across running-state changes.
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    // Enter confirms Chinese/Japanese IME candidates before it submits a draft.
    if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
    if (event.key === "Escape" && quote) {
      event.preventDefault();
      setQuote(null);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  }

  async function choosePermission(mode: string) {
    if (mode === permissionMode) {
      permissionPanel.close(true);
      return;
    }
    if (!onPermissionModeChange || running) return;

    setPendingSelection(mode);
    setPanelError("");
    try {
      await onPermissionModeChange(mode);
      permissionPanel.close(true);
    } catch (cause) {
      setPanelError(cause instanceof Error ? cause.message : "Could not update permission profile.");
    } finally {
      setPendingSelection("");
    }
  }

  const currentPermission = permissionPresentation(permissionMode || "approval", zh);
  const currentModel = modelSnapshot?.current?.model || model || "Model";
  const workspaceName = workspace?.replace(/[\\/]+$/, "").split(/[\\/]/).pop();
  const hasDraftContent = Boolean(value.trim() || quote || attachments.length);
  const canSend = Boolean(
    !disabled
    && !imageNotice.blocked
    && !running
    && (value.trim() || quote || attachments.some((item) => imagesAllowed || !item.isImage))
  );

  const settingsControls = <>
            <span className="composer-toolbar-divider" aria-hidden="true" />

            <div className="composer-control-anchor cp-anchor" ref={permissionPanel.anchorRef}>
              <button
                ref={permissionPanel.triggerRef}
                type="button"
                className={`composer-chip permission-chip composer-refined-control ${permissionPanel.open ? "is-open" : ""}`}
                data-mode={permissionMode || "approval"}
                title={zh ? "权限" : "Permissions"}
                aria-haspopup="menu"
                aria-expanded={permissionPanel.open}
                onClick={permissionPanel.toggle}
              >
                <span className="composer-glyph-well access" aria-hidden="true"><PermissionIcon mode={permissionMode || "approval"} toolbar /></span>
                <span className="composer-permission-label">{currentPermission.label}</span>
                <ChevronDown size={13} className="composer-chip-chevron" />
              </button>

              <ComposerPanel panel={permissionPanel} label={zh ? "权限" : "Permissions"} role="menu" size="m"
                className={`permission-popover ${running ? "is-locked" : ""}`}>
                <PanelHead title={zh ? "权限" : "Permissions"} aside={zh ? "当前对话" : "This conversation"} />
                <div className="cp-body">
                  {availablePermissionModes.map((mode) => {
                    const presentation = permissionPresentation(mode, zh);
                    const active = mode === permissionMode;
                    const pending = pendingSelection === mode;
                    const tone = presentation.tone === "recommended" ? " is-accent" : presentation.tone === "danger" ? " is-warn" : "";
                    return (
                      <button
                        key={mode}
                        type="button"
                        data-ic-row
                        data-cp-item=""
                        data-cp-current={active ? "" : undefined}
                        className={`cp-row is-tall permission-option ${active ? "active" : ""}`}
                        role="menuitemradio"
                        aria-checked={active}
                        disabled={Boolean(pendingSelection) || running || (!onPermissionModeChange && !active)}
                        onClick={() => void choosePermission(mode)}
                      >
                        <span className={`cp-row-lead${presentation.tone === "danger" ? " is-warn" : ""}`}><PermissionIcon mode={mode} /></span>
                        <span className="cp-row-text">
                          <span className="cp-row-title">
                            <span>{presentation.label}</span>
                            <span className={`cp-tag${tone}`}>{presentation.badge}</span>
                          </span>
                          <span className="cp-row-desc">{presentation.description}</span>
                        </span>
                        <span className={`cp-row-end${active ? " is-check" : ""}`} aria-hidden="true">
                          {pending ? <LoaderCircle size={14} strokeWidth={2} /> : active ? <Check size={15} strokeWidth={2} /> : null}
                        </span>
                      </button>
                    );
                  })}
                  {panelError ? <div className="cp-note is-error" role="alert">{panelError}</div> : null}
                </div>
                <PanelFoot>
                  <LockKeyhole size={13} strokeWidth={1.75} aria-hidden="true" />
                  <span>
                    {zh
                      ? (running
                        ? "当前任务结束后才能切换权限；新权限从下一轮开始生效。"
                        : "从下一轮开始生效，不会改变正在执行的操作。")
                      : (running
                        ? "Finish the active turn before changing access. The new access applies from the next turn."
                        : "Applies from the next turn; work already in progress keeps its access.")}
                  </span>
                </PanelFoot>
              </ComposerPanel>
            </div>

            <div className="composer-control-anchor model-control-anchor cp-anchor" ref={modelPanel.anchorRef}>
              <button
                ref={modelPanel.triggerRef}
                type="button"
                className={`composer-chip model-chip composer-refined-control ${modelPanel.open ? "is-open" : ""}`}
                title={currentModel}
                aria-haspopup="dialog"
                aria-expanded={modelPanel.open}
                onClick={() => {
                  if (!modelPanel.open) {
                    void Promise.resolve(onRefreshActiveThread?.()).catch(() => {
                      // The existing model list stays usable if Host status
                      // refresh fails; choosing a model surfaces RPC errors.
                    });
                  }
                  modelPanel.toggle();
                }}
              >
                <span className="composer-glyph-well model" aria-hidden="true"><ComposerControlIcon kind="model" /></span>
                <span className="composer-model-label">{currentModel}</span>
                <ChevronDown size={13} className="composer-chip-chevron" />
              </button>

              <ComposerPanel panel={modelPanel} label={zh ? "模型" : "Model"} size="l" className="model-popover model-manager-popover">
                <ModelPanel
                  runtimeModel={model}
                  snapshot={modelSnapshot ?? null}
                  busy={modelBusy}
                  running={running}
                  accountAuthenticated={accountAuthenticated}
                  onOpenAccount={onOpenAccount}
                  onSwitchProfile={(selection) => selectModel(onModelProfileChange ? () => onModelProfileChange(selection) : undefined)}
                  onSwitchCurrent={(nextModel) => selectModel(onCustomModelChange ? () => onCustomModelChange(nextModel) : undefined)}
                  onConfigureProvider={async (provider, apiKey) => {
                    if (!onConfigureModelProvider) throw new Error("Provider configuration is unavailable.");
                    await onConfigureModelProvider(provider, apiKey);
                  }}
                  onRefresh={async () => {
                    await onRefreshModels?.(true);
                  }}
                  onAddModel={async (input) => {
                    if (!onAddModel) throw new Error("Adding model APIs is unavailable.");
                    await onAddModel(input);
                  }}
                  onDeleteModel={async (selection) => {
                    if (!onDeleteModel) throw new Error("Deleting model APIs is unavailable.");
                    await onDeleteModel(selection);
                  }}
                  onReasoningChange={async (kind, nextValue) => {
                    if (!onReasoningChange) throw new Error("Reasoning control is unavailable.");
                    await onReasoningChange(kind, nextValue);
                  }}
                  onClose={() => modelPanel.close(true)}
                />
              </ComposerPanel>
            </div>

            <ModelUsageIndicator key={accountUsageIdentity} selection={accountAuthenticated ? modelSnapshot?.current?.selection || modelSnapshot?.primary?.selection || "" : ""} zh={zh} />

  </>;
  if (controlsOnly) return <ComposerPanelHost host={panels}><div ref={composerRootRef} className="composer-shared-settings">
    <ComposerToolSelector threadId={threadId} workspace={workspace} selection={toolSelection} onChange={setToolSelection}
      readOnly={Boolean(running)} onInsertSkill={() => {}} />
    {settingsControls}
  </div></ComposerPanelHost>;

  return (
    <ComposerPanelHost host={panels}>
    <div className="composer-wrap composer-refined" ref={composerRootRef}>
      <div
        className={`composer ${focused ? "is-focused" : ""} ${running ? "is-running" : ""} ${panels.mounted ? "has-panel" : ""} ${dragging ? "is-dragging" : ""} ${hasDraftContent ? "has-content" : "is-empty"}`}
        onDragOver={(event) => {
          if (!event.dataTransfer.types.includes("Files")) return;
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node)) return;
          setDragging(false);
        }}
        onDrop={(event) => void onDrop(event)}
      >
        {quote ? <QuoteReplyBar quote={quote} onClear={() => setQuote(null)} /> : null}
        <ComposerAttachmentStrip attachments={attachments} imagesAllowed={imagesAllowed} onRemove={removeAttachment} />

        {modelError ? <p role="alert" className="composer-attach-error">{modelError}</p> : null}
        {attachError ? <p className="composer-attach-error">{attachError}</p> : null}
        {stopError ? <p className="composer-attach-error">Could not stop: {stopError}</p> : null}
        {imageNotice.message ? <p className="composer-image-notice" role="status">{imageNotice.message}</p> : null}

        <div className="composer-input-row">
          <span className="composer-spark" aria-hidden="true"><Sparkles size={15} /></span>
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={onKeyDown}
            onPaste={(event) => void onPaste(event)}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            placeholder={starting
              ? (zh ? "Loom 正在启动，稍等片刻…" : "Loom is starting up…")
              : !accountAuthenticated
              ? (zh ? "登录 Loom 后才能使用模型和发送消息" : "Sign in to Loom to use models and send messages")
              : quote
                ? (zh ? "针对引用内容继续提问…" : "Ask about the quoted content…")
                : zh
                  ? (disabled ? "打开对话，开始工作" : "描述任务，或提出问题…")
                  : (disabled ? "Open a thread to start" : "Describe a task or ask a question…")}
            disabled={disabled || running || submitting}
            rows={1}
          />
        </div>

        <div className="composer-toolbar composer-toolbar-idle">
          <div className="composer-left">
            <ComposerToolSelector threadId={threadId} workspace={workspace} selection={toolSelection} onChange={setToolSelection}
              disabled={disabled || submitting || running}
              onInsertSkill={name => { setValue(`${value}${value && !/\s$/.test(value) ? " " : ""}$${name} `); textareaRef.current?.focus(); }} />
            <button
              type="button"
              className="composer-tool composer-refined-control"
              aria-label={zh ? "添加附件" : "Attach files"}
              title={zh ? "添加附件，也可以粘贴或拖入文件" : "Attach files, or paste and drop them here"}
              onClick={() => void pickAttachments()}
              disabled={disabled}
            >
              <span className="composer-glyph-well attach" aria-hidden="true"><ComposerControlIcon kind="attach" /></span>
            </button>

            {workspace && onOpenWorkspace ? <button type="button" className="composer-tool composer-workspace-button"
              title={`${zh ? "查看工作目录" : "Open workspace files"} · ${workspace}`} aria-label={zh ? "查看工作目录" : "Open workspace files"}
              onClick={onOpenWorkspace}><FolderOpen size={14} /><span>{workspaceName}</span></button> : null}

            {settingsControls}

          </div>

          <div className="composer-right">
            <ComposerDraftTools text={formatQuotedPrompt(quote, value)} attachments={attachments} threadId={threadId} disabled={disabled || submitting} />
            <Crossfade identity={`${Boolean(running)}:${stopping}`}>
              {!running ? <span className={submitting ? "composer-running-label" : "composer-keycap"} role={submitting ? "status" : undefined}>{submitting ? (zh ? "正在发送…" : "Sending…") : "Enter ↵"}</span> : (
                <span className="composer-running-label" role="status">
                  <i aria-hidden="true" />
                  <span className="composer-running-copy">
                    {stopping ? (zh ? "正在停止…" : "Stopping…") : (zh ? "任务进行中" : "Working")}
                  </span>
                </span>
              )}
            </Crossfade>
            <Crossfade identity={String(Boolean(running))}>{running ? (
              <button type="button" className="send-button stop" disabled={stopping} onClick={() => void handleInterrupt()} title="Stop current turn" aria-label="Stop current turn">
                <Square size={12} fill="currentColor" />
              </button>
            ) : (
              <button type="button" className={`send-button ${canSend ? "is-ready" : "is-idle"}`} onClick={() => void submit()} disabled={!canSend || submitting} title="Send" aria-label="Send message">
                <Send size={17} strokeWidth={2} />
              </button>
            )}</Crossfade>
          </div>
        </div>
      </div>
      <div className="composer-hint is-shortcut">{zh ? "Enter 发送 · Shift + Enter 换行" : "Enter to send · Shift + Enter for a new line"}</div>
    </div>
    </ComposerPanelHost>
  );
}
