/**
 * Approval card fixture.
 *
 * Mounts the real Transcript under the renderer's full stylesheet cascade with
 * one approval of each shape the runtime produces, and records every decision
 * the card sends in window.__decisions, so the choices can be checked without a
 * Host.
 *
 *   cd desktop-react && npx vite --port 5199
 *   open http://127.0.0.1:5199/scripts/fixtures/approval-card.html
 *
 *   ?case=plain|command|prefix|escalation|tool    which request to show (default prefix)
 *   ?theme=light|dark                             initial theme (default dark)
 *   ?lang=en                                      English copy
 */
import { createRoot } from "react-dom/client";
import "../../src/App";
import "../../src/components/BootErrorBoundary";
import "../../src/components/GlobalContextMenu";
import { I18nProvider } from "../../src/i18n";
import { applyThemePreference } from "../../src/theme";
import "../../src/renderer-styles";
import { Transcript } from "../../src/components/Transcript";
import type { ApprovalDecision, TranscriptItem } from "../../src/types/loom";

const params = new URLSearchParams(window.location.search);
window.localStorage.setItem("loom.settings.language", params.get("lang") === "en" ? "en" : "zh-CN");
applyThemePreference(params.get("theme") === "light" ? "light" : "dark", { animate: false, syncNative: false });

const base: TranscriptItem = {
  id: "approval:call-1",
  threadId: "thread-approval",
  turnId: "turn-1",
  type: "approval",
  status: "waiting",
  callId: "call-1",
  toolName: "exec",
  effect: "sensitive",
  reason: "unless-trusted policy requires approval for unmatched commands",
};

const CASES: Record<string, TranscriptItem> = {
  plain: { ...base, toolName: "get_weather", availableDecisions: ["accept", "decline"] },
  command: {
    ...base,
    availableDecisions: ["accept", "acceptForSession", "decline"],
    sessionScope: { kind: "command" },
    ruleSandbox: "policy",
  },
  prefix: {
    ...base,
    availableDecisions: ["accept", "acceptForSession", "acceptWithPrefixRule", "decline"],
    sessionScope: { kind: "prefix", prefix: ["git", "status"] },
    proposedPrefixRule: ["git", "status"],
    ruleSandbox: "policy",
  },
  tool: {
    ...base,
    toolName: "apply_patch",
    reason: "Permission profile read-only does not grant mutating; user approval can elevate this call.",
    availableDecisions: ["accept", "acceptForSession", "decline"],
    sessionScope: { kind: "tool", tool: "apply_patch" },
  },
  escalation: {
    ...base,
    reason: "The OS sandbox could not start this command. Approval lets the same bound action run once without OS-level containment.",
    retryReason: "Sandbox denied: git.EXE could not start inside the Windows sandbox: a DLL failed to initialize (STATUS_DLL_INIT_FAILED, 0xC0000142), usually because the AppContainer cannot open something it needs at startup.",
    availableDecisions: ["accept", "acceptForSession", "acceptWithPrefixRule", "decline"],
    sessionScope: { kind: "prefix", prefix: ["git", "status"] },
    proposedPrefixRule: ["git", "status"],
    ruleSandbox: "unsandboxed",
    sandboxDenial: {
      kind: "dll_init",
      startup: true,
      detail: "git.EXE could not start inside the Windows sandbox",
      program: "git.EXE",
      path: "",
      remediation: 'powershell -NoProfile -ExecutionPolicy Bypass -File "C:\\Loom\\resources\\windows-sandbox-prep.ps1"',
    },
  },
};

const decisions: { decision: ApprovalDecision; feedback?: string }[] = [];
(window as unknown as { __decisions: typeof decisions }).__decisions = decisions;

function onApproval(_item: TranscriptItem, decision: ApprovalDecision, feedback?: string) {
  decisions.push({ decision, ...(feedback ? { feedback } : {}) });
}

const item = CASES[params.get("case") ?? "prefix"] ?? CASES.prefix;
// A turn that is still running keeps its approval on screen (a finished one folds).
const ask: TranscriptItem = {
  id: "user-1", threadId: "thread-approval", turnId: "turn-1", type: "user_message", text: "看一下仓库状态", status: "completed",
};

createRoot(document.getElementById("root")!).render(
  <I18nProvider>
    <div style={{ padding: 24, maxWidth: 860 }}>
      <Transcript items={[ask, item]} running currentTurnId="turn-1" workspace="C:\\demo" onApproval={onApproval} onPrompt={() => {}} />
    </div>
  </I18nProvider>,
);
