import {
  ArrowUp,
  Check,
  ChevronDown,
  Cpu,
  Eye,
  FolderCog,
  KeyRound,
  Paperclip,
  X,
  FileText,
  ShieldCheck,
  Smile,
  Sparkles,
  Square,
} from "lucide-react";
import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import type { AddModelInput, Attachment, ModelSnapshot, StickerPreferences } from "../types/loom";
import { ModelPanel } from "./ModelPanel";
import { StickerPanel } from "./StickerPanel";
import "./composer.css";

interface ComposerProps {
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
  onAddModel?(input: AddModelInput): Promise<void> | void;
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

const IMAGE_SUFFIXES = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"]);
const MAX_ATTACHMENTS = 10;

function baseName(value: string): string {
  const parts = value.replaceAll("\\", "/").split("/");
  return parts.at(-1) || value;
}

function looksLikeImage(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && IMAGE_SUFFIXES.has(name.slice(dot).toLowerCase());
}

function formatSize(size: number): string {
  if (size <= 0) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function Composer({
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
  onAddModel,
  onReasoningChange,
  onStickerPreferencesChange,
  imagesAllowed = true,
  onSend,
  onInterrupt,
}: ComposerProps) {
  const [value, setValue] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [attachError, setAttachError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [focused, setFocused] = useState(false);
  const [openPanel, setOpenPanel] = useState<OpenPanel>(null);
  const [pendingSelection, setPendingSelection] = useState("");
  const [panelError, setPanelError] = useState("");
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState("");
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
    if (!incoming.length) return;
    setAttachments((current) => {
      const known = new Set(current.map((item) => item.path));
      const next = [...current];
      let overflowed = false;
      for (const item of incoming) {
        if (known.has(item.path)) continue;
        if (next.length >= MAX_ATTACHMENTS) {
          overflowed = true;
          break;
        }
        known.add(item.path);
        next.push(item);
      }
      if (overflowed) setAttachError(`At most ${MAX_ATTACHMENTS} attachments per message.`);
      return next;
    });
  };

  const attachmentFromPath = (filePath: string, size = 0): Attachment => {
    const name = baseName(filePath);
    return { id: filePath, name, path: filePath, size, isImage: looksLikeImage(name) };
  };

  /** Resolve dropped/pasted items to paths. Files already on disk keep theirs;
   *  a pasted image has none, so its bytes are written to a temp file first. */
  const resolveFiles = async (files: File[]): Promise<Attachment[]> => {
    const bridge = window.loom;
    const resolved: Attachment[] = [];
    for (const file of files) {
      const existing = bridge?.filePathFor?.(file) || "";
      if (existing) {
        resolved.push(attachmentFromPath(existing, file.size));
        continue;
      }
      if (!bridge?.stageTempFile) continue;
      try {
        const bytes = new Uint8Array(await file.arrayBuffer());
        const staged = await bridge.stageTempFile(file.name || "pasted.png", bytes);
        if (staged) {
          const entry = attachmentFromPath(staged, file.size);
          resolved.push({
            ...entry,
            name: file.name || entry.name,
            previewUrl: entry.isImage ? URL.createObjectURL(file) : undefined,
          });
        }
      } catch {
        setAttachError("Could not read one of the attachments.");
      }
    }
    return resolved;
  };

  const onPaste = async (event: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const files = [...event.clipboardData.files];
    if (!files.length) return;
    // Intercepted so the image does not also land in the prompt as pasted
    // rich content that is silently dropped on send.
    event.preventDefault();
    setAttachError("");
    addAttachments(await resolveFiles(files));
  };

  const onDrop = async (event: React.DragEvent) => {
    if (!event.dataTransfer.files.length) return;
    event.preventDefault();
    setDragging(false);
    setAttachError("");
    addAttachments(await resolveFiles([...event.dataTransfer.files]));
  };

  const pickAttachments = async () => {
    const picked = (await window.loom?.pickFiles?.()) ?? [];
    setAttachError("");
    addAttachments(picked.map((filePath) => attachmentFromPath(filePath)));
  };

  const removeAttachment = (id: string) => {
    setAttachments((current) => {
      const target = current.find((item) => item.id === id);
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
      return current.filter((item) => item.id !== id);
    });
  };

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    const input = value.trim();
    const sendable = attachments.filter((item) => imagesAllowed || !item.isImage);
    // An attachment alone is a complete message; an empty composer is not.
    if ((!input && !sendable.length) || disabled || running) return;
    setValue("");
    setAttachments([]);
    setAttachError("");
    setOpenPanel(null);
    await onSend(input, sendable.map((item) => ({ path: item.path, name: item.name })));
  }

  function onKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
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

  const currentPermission = permissionPresentation(permissionMode || "approval");
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

        {attachments.length ? (
          <div className="composer-attachments">
            {attachments.map((item) => {
              const blocked = item.isImage && !imagesAllowed;
              return (
                <span
                  key={item.id}
                  className={`composer-attachment ${blocked ? "is-blocked" : ""}`}
                  title={blocked ? "This model cannot read images" : item.path}
                >
                  {item.previewUrl ? (
                    <img src={item.previewUrl} alt="" className="composer-attachment-thumb" />
                  ) : (
                    <FileText size={13} />
                  )}
                  <span className="composer-attachment-name">{item.name}</span>
                  {formatSize(item.size) ? (
                    <span className="composer-attachment-size">{formatSize(item.size)}</span>
                  ) : null}
                  <button type="button" onClick={() => removeAttachment(item.id)} aria-label={`Remove ${item.name}`}>
                    <X size={12} />
                  </button>
                </span>
              );
            })}
          </div>
        ) : null}

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
            placeholder={disabled ? "Open a thread to start" : running ? "Loom is working…" : "Ask Loom to inspect, build, debug, or automate…"}
            disabled={disabled || running}
            rows={1}
          />
        </div>

        <div className="composer-toolbar">
          <div className="composer-left">
            <button
              type="button"
              className="composer-tool"
              title="Attach files · or paste and drop them straight into the message"
              onClick={() => void pickAttachments()}
              disabled={disabled}
            >
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
                title={currentModel}
                aria-haspopup="menu"
                aria-expanded={openPanel === "model"}
                onClick={() => togglePanel("model")}
              >
                <Cpu size={13} />
                <span>{currentModel}</span>
                <ChevronDown size={13} className="composer-chip-chevron" />
              </button>

              {openPanel === "model" ? (
                <div className="composer-popover model-popover model-manager-popover" role="dialog" aria-label="Model manager">
                  <div className="composer-popover-head">
                    <div className="composer-popover-heading">
                      <span className="composer-popover-icon model"><Cpu size={16} /></span>
                      <div>
                        <strong>Models</strong>
                        <span>Switch runtime models, tune reasoning, or connect a custom API.</span>
                      </div>
                    </div>
                    <span className="composer-popover-context">Runtime</span>
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
                    onAddModel={async (input) => {
                      if (!onAddModel) throw new Error("Adding model APIs is unavailable.");
                      await onAddModel(input);
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
                title="Chat expression settings"
                aria-haspopup="dialog"
                aria-expanded={openPanel === "sticker"}
                onClick={() => togglePanel("sticker")}
              >
                <Smile size={13} />
                <span>{stickersOff ? "Stickers off" : "Stickers"}</span>
                <ChevronDown size={13} className="composer-chip-chevron" />
              </button>

              {openPanel === "sticker" ? (
                <div className="composer-popover sticker-popover" role="dialog" aria-label="Chat expression settings">
                  <div className="composer-popover-head">
                    <div className="composer-popover-heading">
                      <span className="composer-popover-icon model"><Smile size={16} /></span>
                      <div>
                        <strong>Stickers</strong>
                        <span>Tune inline expression without changing the message protocol.</span>
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
            {!running ? <span className="composer-keycap">Enter ↵</span> : <span className="composer-running-label"><i /> {stopping ? "Stopping…" : "Working"}</span>}
            {running ? (
              <button type="button" className="send-button stop" disabled={stopping} onClick={() => void handleInterrupt()} title="Stop current turn" aria-label="Stop current turn">
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
