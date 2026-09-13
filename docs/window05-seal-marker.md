# Window05 seal marker

Window: App Server / Protocol / Electron UI parity

Branch: `codex-appserver-protocol-parity-v1`

Loom base: `b3dc3db50b117615be59b79806a6fd8fd38b02a8`

Final audited Codex main: `36f0dbe796d9bb1a18a0fc0640ed08b3e1d54564`

Status: **SEALED** at the contract/audit level.

Seal conditions satisfied:

- approval responses are correlated to exact thread, turn, durable request identity, and call before runtime resume;
- stale, duplicate, cancelled, and mismatched approval responses fail closed at the app-server boundary;
- `turn/interrupt` requires the exact active turn identity;
- waiting-approval reconnect reconstructs the same durable request identity;
- Loom lifecycle staging uses `approvalStage`, avoiding semantic collision with Codex command-approval `kind`;
- the Electron compatibility transport is explicitly advertised as `correlatedNotification`, not claimed as Codex v2 server-request parity;
- unsupported request families and process-level active-turn recovery remain explicit cross-window dependencies.

Validation state at seal: `contract committed, CI not executed`.

This seal does not assert green CI and does not authorize merging the Draft PR solely on the basis of this marker.
