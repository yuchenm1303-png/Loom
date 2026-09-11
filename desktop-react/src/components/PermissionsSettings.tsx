import {
  Check,
  CircleAlert,
  FolderOpen,
  Globe2,
  Info,
  Monitor,
  ShieldCheck,
  Terminal,
  type LucideIcon,
} from "lucide-react";
import "./settings-permissions.css";

type PermissionMode = "read-only" | "approval" | "workspace" | "full-access" | string;

type PermissionProfile = {
  label: string;
  eyebrow: string;
  description: string;
  tone: "safe" | "balanced" | "workspace" | "broad";
  bullets: string[];
};

const PROFILE_COPY: Record<string, PermissionProfile> = {
  "read-only": {
    label: "Read Only",
    eyebrow: "Safest",
    description: "Inspect context, files, and available tools without allowing workspace changes.",
    tone: "safe",
    bullets: ["Read and inspect context", "Workspace changes blocked", "Best for review and investigation"],
  },
  approval: {
    label: "Approval",
    eyebrow: "Recommended",
    description: "Let Loom handle routine work while asking before sensitive, privileged, or destructive actions.",
    tone: "balanced",
    bullets: ["Routine actions can proceed", "Sensitive actions ask first", "Balanced for daily Agent work"],
  },
  workspace: {
    label: "Workspace",
    eyebrow: "Project scoped",
    description: "Allow routine edits and commands inside the active workspace with fewer interruptions.",
    tone: "workspace",
    bullets: ["Optimized for project work", "Workspace changes are streamlined", "Broader actions remain policy-bound"],
  },
  "full-access": {
    label: "Full Access",
    eyebrow: "Highest access",
    description: "Give Loom the broadest authority available on this machine for a trusted local environment.",
    tone: "broad",
    bullets: ["Broad local authority", "Approval prompts are minimized", "Use only on a trusted machine"],
  },
};

function profileFor(mode: PermissionMode): PermissionProfile {
  return PROFILE_COPY[mode] ?? {
    label: mode
      .replaceAll("_", " ")
      .replaceAll("-", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase()),
    eyebrow: "Runtime profile",
    description: "Permission profile reported by the current Loom runtime.",
    tone: "balanced",
    bullets: ["Runtime-defined behavior", "Capability exposure still applies", "Thread overrides remain available"],
  };
}

function boundaryText(mode: PermissionMode, kind: "workspace" | "shell"): string {
  if (kind === "workspace") {
    if (mode === "read-only") return "Changes blocked";
    if (mode === "approval") return "Sensitive writes ask first";
    if (mode === "workspace") return "Project-scoped writes";
    if (mode === "full-access") return "Broad write authority";
    return "Controlled by profile";
  }
  if (mode === "read-only") return "Non-mutating use only";
  if (mode === "approval") return "Privileged actions ask first";
  if (mode === "workspace") return "Workspace-oriented execution";
  if (mode === "full-access") return "Broad execution authority";
  return "Controlled by profile";
}

function BoundaryRow({
  icon: Icon,
  title,
  detail,
  value,
  enabled = true,
  onOpen,
}: {
  icon: LucideIcon;
  title: string;
  detail: string;
  value: string;
  enabled?: boolean;
  onOpen?: () => void;
}) {
  const content = (
    <>
      <span className={`permission-boundary-icon ${enabled ? "" : "off"}`}><Icon size={16} strokeWidth={1.8} /></span>
      <div className="permission-boundary-copy"><strong>{title}</strong><span>{detail}</span></div>
      <span className={`permission-boundary-value ${enabled ? "" : "off"}`}>{value}</span>
    </>
  );
  return onOpen ? <button type="button" className="permission-boundary-row actionable" onClick={onOpen}>{content}</button> : <div className="permission-boundary-row">{content}</div>;
}

export function PermissionsSettings({
  permissionModes,
  defaultPermissionMode,
  running,
  computerEnabled,
  browserEnabled,
  onOpenComputer,
  onOpenBrowser,
}: {
  permissionModes?: string[];
  defaultPermissionMode?: string;
  running: boolean;
  computerEnabled: boolean;
  browserEnabled: boolean;
  onOpenComputer(): void;
  onOpenBrowser(): void;
}) {
  const modes = permissionModes?.length ? permissionModes : ["read-only", "approval", "workspace", "full-access"];
  const defaultMode: PermissionMode = defaultPermissionMode || "approval";
  const current = profileFor(defaultMode);

  return (
    <>
      <div className="settings-page-heading permissions-page-heading">
        <div>
          <span className="settings-eyebrow">Execution safety</span>
          <h1>Permissions</h1>
          <p>Understand exactly how much authority Loom receives before it reads, edits, executes, browses, or controls the desktop.</p>
        </div>
        <span className={`permission-heading-state ${running ? "running" : ""}`}><i />{running ? "Agent turn active" : "Policy ready"}</span>
      </div>

      <div className={`permission-overview-card tone-${current.tone}`}>
        <div className="permission-overview-main">
          <span className="permission-overview-icon"><ShieldCheck size={20} strokeWidth={1.8} /></span>
          <div>
            <span className="settings-eyebrow">Default for new conversations</span>
            <strong>{current.label}</strong>
            <p>{current.description}</p>
          </div>
        </div>
        <div className="permission-overview-facts">
          <div><span>Protection</span><strong>{current.eyebrow}</strong></div>
          <div><span>Thread override</span><strong>Available in chat</strong></div>
          <div><span>Capability layer</span><strong>Still enforced</strong></div>
        </div>
      </div>

      {running ? (
        <div className="permission-runtime-note"><CircleAlert size={16} /><div><strong>An Agent turn is currently active.</strong><span>The running thread keeps its current permission mode. Thread-level changes are made from the chat composer.</span></div></div>
      ) : null}

      <section className="settings-section permission-profiles-section">
        <div className="settings-section-heading"><h2>Permission profiles</h2><p>Profiles define the execution boundary; individual conversations can still override the default from the chat surface.</p></div>
        <div className="permission-profile-grid">
          {modes.map((mode) => {
            const profile = profileFor(mode);
            const selected = mode === defaultMode;
            return (
              <div className={`permission-profile-card tone-${profile.tone} ${selected ? "selected" : ""}`} key={mode}>
                <div className="permission-profile-head">
                  <span className="permission-profile-icon"><ShieldCheck size={17} strokeWidth={1.8} /></span>
                  <div><span className="permission-profile-eyebrow">{profile.eyebrow}</span><strong>{profile.label}</strong></div>
                  {selected ? <span className="permission-default-badge"><Check size={12} />Default</span> : null}
                </div>
                <p>{profile.description}</p>
                <div className="permission-profile-bullets">
                  {profile.bullets.map((item) => <span key={item}><i /><span>{item}</span></span>)}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="settings-section">
        <div className="settings-section-heading"><h2>Safety boundaries</h2><p>Permission policy and capability exposure work together. A disabled capability stays unavailable even under Full Access.</p></div>
        <div className="settings-card permission-boundary-list">
          <BoundaryRow icon={Monitor} title="Computer control" detail="Screenshots, UI Automation, mouse, and keyboard actions." value={computerEnabled ? "Available · profile controlled" : "Capability off"} enabled={computerEnabled} onOpen={onOpenComputer} />
          <BoundaryRow icon={Globe2} title="Browser automation" detail="Browser sessions, page interaction, and compatible local browser attachment." value={browserEnabled ? "Available · profile controlled" : "Capability off"} enabled={browserEnabled} onOpen={onOpenBrowser} />
          <BoundaryRow icon={Terminal} title="Shell & processes" detail="Commands and managed process execution remain subject to the selected permission boundary." value={boundaryText(defaultMode, "shell")} />
          <BoundaryRow icon={FolderOpen} title="Workspace writes" detail="File creation, edits, and project changes are constrained by the selected profile." value={boundaryText(defaultMode, "workspace")} />
        </div>
      </section>

      <div className="permission-explainer">
        <Info size={16} />
        <div><strong>Permissions do not turn tools on by themselves.</strong><span>Capabilities decide which tool families Loom can see; permission profiles decide how aggressively those tools may act. For the tightest boundary, disable unused capabilities as well.</span></div>
      </div>
    </>
  );
}
