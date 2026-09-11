import {
  Check,
  Keyboard,
  MessageSquareText,
  PanelLeft,
  PanelRight,
  Paperclip,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Square,
  Type,
  type LucideIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import {
  DEFAULT_SHORTCUTS,
  findShortcutConflict,
  formatShortcut,
  normalizeShortcut,
  shortcutFromKeyboardEvent,
  type ShortcutCommandId,
  type ShortcutSettings,
} from "../keyboardShortcuts";
import "./settings-shortcuts.css";

type ShortcutCommand = {
  id: ShortcutCommandId;
  label: string;
  detail: string;
  group: "Navigation" | "Conversation" | "Workspace";
  icon: LucideIcon;
};

const COMMANDS: ShortcutCommand[] = [
  { id: "newConversation", label: "New conversation", detail: "Start a fresh conversation in the current workspace.", group: "Navigation", icon: Plus },
  { id: "searchConversations", label: "Search conversations", detail: "Open and focus conversation search in the sidebar.", group: "Navigation", icon: Search },
  { id: "openSettings", label: "Open settings", detail: "Open Settings from anywhere, or close it when already open.", group: "Navigation", icon: Settings2 },
  { id: "focusComposer", label: "Focus composer", detail: "Jump directly to the active conversation input.", group: "Conversation", icon: MessageSquareText },
  { id: "attachFiles", label: "Attach files", detail: "Open the attachment picker for the active conversation.", group: "Conversation", icon: Paperclip },
  { id: "stopTask", label: "Stop active task", detail: "Interrupt the current Agent turn when Loom is working.", group: "Conversation", icon: Square },
  { id: "toggleSidebar", label: "Toggle sidebar", detail: "Show or hide projects and conversation history.", group: "Workspace", icon: PanelLeft },
  { id: "toggleInspector", label: "Toggle inspector", detail: "Show or hide runtime details and tool activity.", group: "Workspace", icon: PanelRight },
];

const GROUPS: ShortcutCommand["group"][] = ["Navigation", "Conversation", "Workspace"];
const COMMAND_LABELS = Object.fromEntries(COMMANDS.map((command) => [command.id, command.label])) as Record<ShortcutCommandId, string>;

function isSafeGlobalShortcut(value: string, command: ShortcutCommandId): boolean {
  const normalized = normalizeShortcut(value);
  if (!normalized) return false;
  if (command === "stopTask" && normalized === "Escape") return true;
  const pieces = normalized.split("+");
  if (pieces.some((piece) => ["Ctrl", "Alt", "Shift", "Meta"].includes(piece))) return true;
  return /^F\d{1,2}$/.test(pieces.at(-1) || "");
}

function ShortcutKeys({ value }: { value: string }) {
  const normalized = normalizeShortcut(value);
  const keys = normalized ? normalized.split("+") : [];
  if (!keys.length) return <span className="shortcut-unassigned">Unassigned</span>;
  return <span className="shortcut-key-sequence">{keys.map((key) => <kbd key={key}>{key === "Escape" ? "Esc" : key}</kbd>)}</span>;
}

export function KeyboardShortcutsSettings({
  shortcuts,
  onChange,
  onReset,
  onResetAll,
}: {
  shortcuts: ShortcutSettings;
  onChange(id: ShortcutCommandId, value: string): Promise<void> | void;
  onReset(id: ShortcutCommandId): Promise<void> | void;
  onResetAll(): Promise<void> | void;
}) {
  const [query, setQuery] = useState("");
  const [recording, setRecording] = useState<ShortcutCommandId | null>(null);
  const [preview, setPreview] = useState("");
  const [error, setError] = useState("");

  const customizedCount = useMemo(
    () => COMMANDS.filter((command) => normalizeShortcut(shortcuts[command.id]) !== normalizeShortcut(DEFAULT_SHORTCUTS[command.id])).length,
    [shortcuts],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return COMMANDS;
    return COMMANDS.filter((command) => `${command.label} ${command.detail} ${command.group}`.toLowerCase().includes(needle));
  }, [query]);

  const beginRecording = (id: ShortcutCommandId) => {
    setRecording(id);
    setPreview("");
    setError("");
  };

  const handleRecord = async (event: React.KeyboardEvent<HTMLButtonElement>, command: ShortcutCommand) => {
    if (recording !== command.id) return;
    event.preventDefault();
    event.stopPropagation();

    if (event.key === "Backspace" && !event.ctrlKey && !event.altKey && !event.shiftKey && !event.metaKey) {
      setRecording(null);
      setPreview("");
      setError("");
      return;
    }

    const candidate = shortcutFromKeyboardEvent(event);
    if (!candidate) {
      const modifiers = [event.ctrlKey ? "Ctrl" : "", event.altKey ? "Alt" : "", event.shiftKey ? "Shift" : "", event.metaKey ? "Meta" : ""].filter(Boolean);
      setPreview(modifiers.join(" + "));
      return;
    }

    setPreview(formatShortcut(candidate));
    if (!isSafeGlobalShortcut(candidate, command.id)) {
      setError("Global shortcuts need Ctrl, Alt, Shift, Meta, or an F-key so normal typing stays safe.");
      return;
    }

    const conflict = findShortcutConflict(shortcuts, command.id, candidate);
    if (conflict) {
      setError(`${formatShortcut(candidate)} is already assigned to ${COMMAND_LABELS[conflict]}.`);
      return;
    }

    setError("");
    await onChange(command.id, candidate);
    setRecording(null);
    setPreview("");
  };

  return (
    <>
      <div className="settings-page-heading settings-heading-with-action shortcuts-page-heading">
        <div>
          <span className="settings-eyebrow">Productivity</span>
          <h1>Keyboard shortcuts</h1>
          <p>Navigate Loom, control the active Agent turn, and personalize the commands you use most.</p>
        </div>
        <button className="mature-action-button" type="button" onClick={() => void onResetAll()} disabled={customizedCount === 0}>
          <RefreshCw size={14} />Reset all
        </button>
      </div>

      <div className="shortcuts-overview-card">
        <div className="shortcuts-overview-icon"><Keyboard size={19} /></div>
        <div className="shortcuts-overview-copy">
          <span className="settings-eyebrow">Live keymap</span>
          <strong>{COMMANDS.length} global commands</strong>
          <p>Custom bindings apply immediately across the desktop app and persist with your Loom settings.</p>
        </div>
        <div className="shortcuts-overview-stats">
          <div><strong>{GROUPS.length}</strong><span>Groups</span></div>
          <div><strong>{customizedCount}</strong><span>Customized</span></div>
          <div><strong>2</strong><span>Editor keys</span></div>
        </div>
      </div>

      <section className="settings-section shortcuts-command-section">
        <div className="shortcuts-toolbar">
          <div className="settings-section-heading"><h2>Global commands</h2><p>Click a shortcut, then press the replacement key combination. Backspace cancels recording.</p></div>
          <label className="shortcut-search"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search shortcuts" /></label>
        </div>

        {error ? <div className="shortcut-error">{error}</div> : null}

        <div className="shortcut-groups">
          {GROUPS.map((group) => {
            const commands = filtered.filter((command) => command.group === group);
            if (!commands.length) return null;
            return (
              <div className="shortcut-group" key={group}>
                <div className="shortcut-group-title"><span>{group}</span><span>{commands.length}</span></div>
                <div className="settings-card shortcut-command-list">
                  {commands.map((command) => {
                    const Icon = command.icon;
                    const active = recording === command.id;
                    const customized = normalizeShortcut(shortcuts[command.id]) !== normalizeShortcut(DEFAULT_SHORTCUTS[command.id]);
                    return (
                      <div className={`shortcut-command-row ${active ? "recording" : ""}`} key={command.id}>
                        <span className="shortcut-command-icon"><Icon size={16} strokeWidth={1.75} /></span>
                        <div className="shortcut-command-copy"><strong>{command.label}</strong><span>{command.detail}</span></div>
                        <div className="shortcut-command-actions">
                          {customized ? <button type="button" className="shortcut-reset-one" onClick={() => void onReset(command.id)} title={`Reset ${command.label}`}><RefreshCw size={12.5} /></button> : null}
                          <button
                            type="button"
                            className={`shortcut-recorder ${active ? "active" : ""}`}
                            onClick={() => beginRecording(command.id)}
                            onKeyDown={(event) => void handleRecord(event, command)}
                            aria-label={`Change shortcut for ${command.label}`}
                          >
                            {active ? <span className="shortcut-recording-copy"><i />{preview || "Press shortcut…"}</span> : <ShortcutKeys value={shortcuts[command.id]} />}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
          {!filtered.length ? <div className="settings-empty-state shortcut-empty">No shortcuts match “{query}”.</div> : null}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-heading"><h2>Composer editing</h2><p>Text editing bindings stay fixed so sending and multiline writing remain predictable.</p></div>
        <div className="settings-card shortcut-fixed-list">
          <div className="shortcut-fixed-row"><span className="shortcut-command-icon"><MessageSquareText size={16} /></span><div><strong>Send message</strong><span>Submit the current prompt and attachments.</span></div><span className="shortcut-fixed-badge"><Check size={12} />Fixed</span><ShortcutKeys value="Enter" /></div>
          <div className="shortcut-fixed-row"><span className="shortcut-command-icon"><Type size={16} /></span><div><strong>New line</strong><span>Insert a line break without sending.</span></div><span className="shortcut-fixed-badge"><Check size={12} />Fixed</span><ShortcutKeys value="Shift+Enter" /></div>
        </div>
      </section>

      <div className="settings-callout shortcuts-callout"><Keyboard size={16} /><div><strong>Global shortcut customization is live.</strong><span>Bindings are checked for conflicts before they are saved. Loom ignores custom global bindings while you type normally unless the shortcut includes a modifier.</span></div></div>
    </>
  );
}
