import {
  ArrowUp,
  Check,
  ChevronDown,
  Cpu,
  Eye,
  FolderCog,
  KeyRound,
  LockKeyhole,
  Paperclip,
  ShieldCheck,
  Smile,
  Sparkles,
  Square,
} from "lucide-react";
import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import type { AddModelInput, Attachment, ModelSnapshot, StickerPreferences } from "../types/loom";
import { useI18n } from "../i18n";
import { useMotionPresence } from "../motion/useMotionPresence";
import { ModelPanel } from "./ModelPanel";
import { ComposerAttachmentStrip } from "./ComposerAttachmentStrip";
import {
  MAX_COMPOSER_ATTACHMENTS,
  appendComposerAttachments,
  attachmentFromPath,
  releaseAttachmentPreview,
  resolveComposerFiles,
} from "./composerAttachments";
import { StickerPanel } from "./StickerPanel";
import { QuoteReplyBar, formatQuotedPrompt, useQuoteReply } from "./quoteReply";
import "./composer.css";
import "./composer-attachment-polish.css";
import "./composer-stability.css";
import "./composer-control-pills.css";

interface ComposerProps {
  threadId?: string;
  disabled?: boolean;
  running?: boolean;
  model?: string;
  modelSnapshot?: ModelSnapshot | null;
  modelBusy?: boolean;
  permissionMode?: string;
  permissionModes?: string[];
  stickerPreferences?: StickerPreferences | null;
  onPermissionModeChange?(mode: string): Promise<void> | void;
  onModelProfileChange?(selection: string): Promise<void> | void;
  onCustomModelChange?(model: string): Promise<void> | void;
  onConfigureModelProvider?(provider: string, apiKey: string): Promise<void> | void;
  onAddModel?(input: AddModelInput): Promise<void> | void;
  onDeleteModel?(selection: string): Promise<void> | void;
  onReasoningChange?(kind: string, value: string): Promise<void> | void;
  onStickerPreferencesChange?(preferences: StickerPreferences): Promise<void> | void;
  /** True when the bound model was declared able to read images. */
  imagesAllowed?: boolean;
  onSend(input: string, attachments: { path: string; name: string }[]): Promise<void> | void;
  onInterrupt(): Promise<void> | void;
}

type OpenPanel = "permission" | "model" | "sticker" | null;

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

function PermissionIcon({ mode }: { mode: string }) {
  if (mode === "read-only") return <Eye size={15} />;
  if (mode === "workspace") return <FolderCog size={15} />;
  if (mode === "full-access") return <KeyRound size={15} />;
  return <ShieldCheck size={15} />;
}

export function Composer({
  threadId,
  disabled,
  running,
  model,
  modelSnapshot,
  modelBusy,
  permissionMode,
  permissionModes,
  stickerPreferences,
  onPermissionModeChange,
  onModelProfileChange,
  onCustomModelChange,
  onConfigureModelProvider,
  onAddModel,
  onDeleteModel,
  onReasoningChange,
  onStickerPreferencesChange,
  imagesAllowed = true,
  onSend,
  onInterrupt,
}: ComposerProps) {
  const { language } = useI18n();
  const zh = language === "zh-CN";
  const [value, setValue] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attachError, setAttachError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [focused, setFocused] = useState(false);
  const [openPanel, setOpenPanel] = useState<OpenPanel>(null);
  const panelPresence = useMotionPresence(Boolean(openPanel), 215);
  const lastOpenPanelRef = useRef<Exclude<OpenPanel, null> | null>(openPanel);
  if (openPanel) lastOpenPanelRef.current = openPanel;
  const renderedPanel = openPanel ?? (panelPresence.mounted ? lastOpenPanelRef.current : null);
  const [pendingSelection, setPendingSelection] = useState("");
  const [panelError, setPanelError] = useState("");
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState("");
  const [quote, setQuote] = useQuoteReply();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const composerRootRef = useRef<HTMLDivElement | null>(null);

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
    setQuote(null);
  }, [threadId, setQuote]);

  useEffect(() => {
    function handlePointerDown(event: PointerEvent) {
      if (!composerRootRef.current?.contains(event.target as Node)) {
        setOpenPanel(null);
        setPanelError("");
      }
    }

    function handleEscape(event: KeyboardEvent) {
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

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const typedInput = value.trim();
    const input = formatQuotedPrompt(quote, typedInput);
    const sendable = attachments.filter((item) => imagesAllowed || !item.isImage);
    // A quote or attachment can carry context on its own; a truly empty composer cannot.
    if ((!typedInput && !quote && !sendable.length) || disabled || running) return;
    setValue("");
    setQuote(null);
    setAttachments([]);
    setAttachError("");
    setOpenPanel(null);
    await onSend(input, sendable.map((item) => ({ path: item.path, name: item.name })));
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
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

  const currentPermission = permissionPresentation(permissionMode || "approval", zh);
  const currentModel = modelSnapshot?.current?.model || model || "Model";
  const stickersOff = stickerPreferences?.frequency === 0;

  return (
    <div className="composer-wrap" ref={composerRootRef}>
      <form
        className={`composer ${focused ? "is-focused" : ""} ${running ? "is-running" : ""} ${openPanel ? "has-panel" : ""} ${dragging ? "is-dragging" : ""}`}
        onSubmit={submit}
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
        <span className="composer-glow" aria-hidden="true" />

        {quote ? <QuoteReplyBar quote={quote} onClear={() => setQuote(null)} /> : null}
        <ComposerAttachmentStrip attachments={attachments} imagesAllowed={imagesAllowed} onRemove={removeAttachment} />

        {attachError ? <p className="composer-attach-error">{attachError}</p> : null}
        {stopError ? <p className="composer-attach-error">Could not stop: {stopError}</p> : null}
        {!imagesAllowed && attachments.some((item) => item.isImage) ? (
          <p className="composer-attach-error">
            This model is not set up to read images. Other files still work — they are saved into the workspace for the agent to read.
          </p>
        ) : null}

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
            placeholder={quote
              ? (zh ? "针对引用内容继续提问…" : "Ask about the quoted content…")
              : zh
                ? (disabled ? "打开对话，开始工作" : "描述任务，或提出问题…")
                : (disabled ? "Open a thread to start" : "Describe a task or ask a question…")}
            disabled={disabled || running}
            rows={1}
          />
        </div>

        <div className="composer-toolbar composer-toolbar-idle">
          <div className="composer-left">
            <button
              type="button"
              className="composer-tool"
              title={zh ? "添加附件，也可以粘贴或拖入文件" : "Attach files, or paste and drop them here"}
              onClick={() => void pickAttachments()}
              disabled={disabled}
            >
              <Paperclip size={15} />
              <span>{zh ? "附件" : "Attach"}</span>
            </button>
            <span className="composer-divider" />

            <div className="composer-control-anchor">
              <button
                type="button"
                className={`composer-chip permission-chip ${openPanel === "permission" ? "is-open" : ""}`}
                title={zh ? "权限设置" : "Permission profile"}
                aria-haspopup="menu"
                aria-expanded={openPanel === "permission"}
                onClick={() => togglePanel("permission")}
              >
                <ShieldCheck size={13} />
                <span>{currentPermission.label}</span>
                <ChevronDown size={13} className="composer-chip-chevron" />
              </button>

              {renderedPanel === "permission" ? (
                <div className={`composer-popover permission-popover ${running ? "is-locked" : ""}`} data-motion-phase={panelPresence.phase} role="menu" aria-label="Permission profiles">
                  <div className="composer-popover-head">
                    <div className="composer-popover-heading">
                      <span className="composer-popover-icon permission"><ShieldCheck size={16} /></span>
                      <div>
                        <strong>{zh ? "权限设置" : "Permission profile"}</strong>
                        <span>{zh ? "选择当前对话的操作权限。" : "Choose access for this conversation."}</span>
                      </div>
                    </div>
                    <span className="composer-popover-context">{zh ? "当前对话" : "Thread"}</span>
                  </div>

                  <div className="composer-option-list">
                    {availablePermissionModes.map((mode) => {
                      const presentation = permissionPresentation(mode, zh);
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
                  <div className="composer-popover-footnote permission-footnote">
                    <LockKeyhole size={13} strokeWidth={1.8} />
                    <span>
                      {zh
                        ? (running
                          ? "当前任务结束后才能切换权限；新权限会从下一轮开始生效。"
                          : "权限切换会从下一轮开始生效，不会改变已经执行中的操作。")
                        : (running
                          ? "Finish the active turn before changing access. The new profile applies from the next turn."
                          : "Permission changes apply from the next turn and do not alter work already in progress.")}
                    </span>
                  </div>
                </div>
              ) : null}
            </div>

            <div className="composer-control-anchor model-control-anchor">
              <button
                type="button"
                className={`composer-chip model-chip ${openPanel === "model" ? "is-open" : ""}`}
                title={currentModel}
                aria-haspopup="menu"
                aria-expanded={openPanel === "model"}
                onClick={() => togglePanel("model")}
              >
                <Cpu size={13} />
                <span>{currentModel}</span>
                <ChevronDown size={13} className="composer-chip-chevron" />
              </button>

              {renderedPanel === "model" ? (
                <div className="composer-popover model-popover model-manager-popover" data-motion-phase={panelPresence.phase} role="dialog" aria-label="Model manager">
                  <div className="composer-popover-head">
                    <div className="composer-popover-heading">
                      <span className="composer-popover-icon model"><Cpu size={16} /></span>
                      <div>
                        <strong>{zh ? "模型" : "Models"}</strong>
                        <span>{zh ? "选择当前会话使用的模型" : "Choose the model for this conversation"}</span>
                      </div>
                    </div>
                  </div>

                  <ModelPanel
                    runtimeModel={model}
                    snapshot={modelSnapshot ?? null}
                    busy={modelBusy}
                    running={running}
                    onSwitchProfile={async (selection) => {
                      if (!onModelProfileChange) throw new Error("Model switching is unavailable.");
                      await onModelProfileChange(selection);
                    }}
                    onSwitchCurrent={async (nextModel) => {
                      if (!onCustomModelChange) throw new Error("Custom model switching is unavailable.");
                      await onCustomModelChange(nextModel);
                    }}
                    onConfigureProvider={async (provider, apiKey) => {
                      if (!onConfigureModelProvider) throw new Error("Provider configuration is unavailable.");
                      await onConfigureModelProvider(provider, apiKey);
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
                    onClose={() => setOpenPanel(null)}
                  />
                </div>
              ) : null}
            </div>

            <div className="composer-control-anchor sticker-control-anchor">
              <button
                type="button"
                className={`composer-chip sticker-chip ${openPanel === "sticker" ? "is-open" : ""}`}
                title={zh ? "表情设置" : "Expression settings"} aria-label={zh ? "表情设置" : "Expression settings"}
                aria-haspopup="dialog"
                aria-expanded={openPanel === "sticker"}
                onClick={() => togglePanel("sticker")}
              >
                <Smile size={13} />
                <span>{stickersOff ? "Stickers off" : "Stickers"}</span>
                <ChevronDown size={13} className="composer-chip-chevron" />
              </button>

              {renderedPanel === "sticker" ? (
                <div className="composer-popover sticker-popover" data-motion-phase={panelPresence.phase} role="dialog" aria-label="Chat expression settings">
                  <div className="composer-popover-head">
                    <div className="composer-popover-heading">
                      <span className="composer-popover-icon model"><Smile size={16} /></span>
                      <div>
                        <strong>{zh ? "表情" : "Stickers"}</strong>
                        <span>{zh ? "调整回复中的表情风格与频率。" : "Adjust expression style and frequency."}</span>
                      </div>
                    </div>
                    <span className="composer-popover-context">Runtime</span>
                  </div>
                  <StickerPanel
                    preferences={stickerPreferences}
                    disabled={running || !onStickerPreferencesChange}
                    onSave={async (preferences) => {
                      if (!onStickerPreferencesChange) throw new Error("Sticker settings are unavailable.");
                      await onStickerPreferencesChange(preferences);
                    }}
                  />
                </div>
              ) : null}
            </div>
          </div>

          <div className="composer-right">
            {!running ? <span className="composer-keycap">Enter ↵</span> : <span className="composer-running-label" role="status"><i aria-hidden="true" /><span className="composer-running-copy">{stopping ? (zh ? "正在停止…" : "Stopping…") : (zh ? "任务进行中" : "Working")}</span></span>}
            {running ? (
              <button type="button" className="send-button stop" disabled={stopping} onClick={() => void handleInterrupt()} title="Stop current turn" aria-label="Stop current turn">
                <Square size={12} fill="currentColor" />
              </button>
            ) : (
              <button type="submit" className="send-button" disabled={disabled || (!value.trim() && !quote && !attachments.some((item) => imagesAllowed || !item.isImage))} title="Send" aria-label="Send message">
                <ArrowUp size={17} strokeWidth={2.2} />
              </button>
            )}
          </div>
        </div>
      </form>
      <div className="composer-hint">{zh ? "Enter 发送 · Shift + Enter 换行" : "Enter to send · Shift + Enter for a new line"}</div>
    </div>
  );
}