# Window05 final audit addendum

This addendum supersedes two parts of `docs/codex-app-server-protocol-parity-v1.md`: the final Codex baseline and the Loom approval lifecycle field name.

## Final upstream baseline

- Initial implementation baseline: `openai/codex@1715e55076737158ba61d43158ede504de6d4ce1`.
- Final audited Codex `main`: `openai/codex@36f0dbe796d9bb1a18a0fc0640ed08b3e1d54564`.
- The only intervening upstream change is Windows Desktop ownership / ACL / provisioning work. It does not alter the app-server approval, callback, reconnect, interrupt, or item-lifecycle contracts used by Window05.

No redesign of the Window05 implementation or tests is required from this upstream drift.

## Approval field correction

Codex v2 `CommandExecutionRequestApprovalParams.kind` identifies the action under review and is currently `command` or `writeStdin`. It is not an initial/network/retry lifecycle stage.

The first Loom compatibility draft used the name `kind` for its own approval stage. Final audit corrected that naming collision:

- Loom now emits `approvalStage` for its compatibility lifecycle stage.
- The serializer no longer reads generic event `kind` as an approval stage.
- Structured future stage input uses `approval_stage` / `approvalStage`.
- `retryReason` remains a Loom compatibility extension.
- `networkApprovalContext` remains a structured pass-through field.
- Loom does not emit a Codex command-approval `kind` until the runtime and transport can truthfully represent that Codex action boundary.

Current wire excerpt:

```json
{
  "requestId": "evt-approval-7",
  "threadId": "thread-1",
  "turnId": "turn-9",
  "itemId": "tool:call-1",
  "approvalItemId": "approval:call-1",
  "callId": "call-1",
  "requestType": "toolExecution",
  "approvalStage": "initial",
  "retryReason": null,
  "availableDecisions": ["accept", "decline"]
}
```

The response remains correlated to exact `threadId + turnId + requestId + callId` before runtime resume.

## Accepted partials

Window05 intentionally remains an adapted compatibility layer rather than wire-identical Codex v2:

- Electron transport still uses `correlatedNotification -> approval/respond`, not true server-initiated JSON-RPC requests.
- Loom currently exposes only `accept` / `decline`, while Codex supports additional decisions.
- file-change approval, MCP elicitation/approval, and request-user-input remain blocked on explicit runtime boundaries.
- process-level active-turn rejoin remains weaker than Codex and is a cross-window recovery dependency.

These are documented gaps, not invented capabilities.

## Validation state

Observed PR jobs had no runner execution and no steps. The correct statement remains:

`contract committed, CI not executed`

No pytest, TypeScript, build, or runtime green claim is made from those workflow records.

## Window05 status

With the field collision corrected, final upstream drift audited, and remaining gaps explicitly classified, Window05 is accepted at the contract/audit level and may be sealed.
