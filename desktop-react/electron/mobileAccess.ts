// What a paired phone may ask this Host to do. Everything else is refused here,
// whatever the gateway forwarded: a phone token must never become a way to read
// the clipboard, change a model key or reach an RPC nobody reviewed for it.
//
// The list grows with the phone's features. Add a method when a mobile screen
// needs it, not before. services/loom_web_gateway/app.py mirrors MOBILE_OPERATIONS.

export const MOBILE_PROTOCOL = 1;

export const MOBILE_OPERATIONS: ReadonlySet<string> = new Set([
  "connect",
  "call",
  "listModels",
  "switchCurrentModel",
  "switchModelProfile",
  "setReasoning",
  "queryModelUsage",
  "readLocalImage",
  "readLocalMedia",
  "readLocalArtifact",
  "stageTempFile",
  "hostUpdateStatus",
]);

export const MOBILE_RPC_METHODS: ReadonlySet<string> = new Set([
  "thread/list",
  "thread/read",
  "thread/start",
  "thread/resume",
  "thread/fork",
  "thread/handoff_status",
  "thread/handoff_retry",
  "thread/rename",
  "thread/archive",
  "thread/delete",
  "thread/context",
  "thread/compact",
  "thread/set_permission_mode",
  "turn/start",
  "turn/steer",
  "turn/interrupt",
  "turn/pause",
  "turn/resume",
  "approval/respond",
  "approvalRules/list",
  "approvalRules/remove",
  "project/list",
  "project/create",
  "project/rename",
  "project/set_instructions",
  "projectRun/list",
  "projectRun/get",
  "projectRun/overview",
  "projectAgent/peek",
  "projectAgent/history",
  "projectRun/prepareWorkspace",
  "projectRun/commitWorkspace",
  "projectRun/pause",
  "projectRun/resume",
  "projectRun/cancel",
  "projectRun/recover",
  "projectRun/steer",
  "projectRun/approval",
  "projectWeave/start",
  "projectWeave/dismiss",
  "projectWeave/goal",
  "projectProgram/attach",
  "tools/catalog",
  "agent/list",
]);

export function mobileInvokeAllowed(operation: string, args: readonly unknown[]): boolean {
  if (!MOBILE_OPERATIONS.has(operation)) return false;
  return operation !== "call" || MOBILE_RPC_METHODS.has(String(args[0] ?? ""));
}
