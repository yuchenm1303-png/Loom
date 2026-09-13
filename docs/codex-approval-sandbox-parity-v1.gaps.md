# Intentional gaps after approval/sandbox parity v1

These are intentionally not represented as complete parity:

1. **Exec-policy amendments** — Codex has `ApprovedExecpolicyAmendment` and a
   persistent exec-policy rule engine. Loom only transports a proposed prefix on
   the exec action in this slice; it does not persist or enforce an amended rule.
2. **Existing-process stdin review** — Codex compares retained launch permissions
   and current policy before deciding whether input to an existing process needs
   review. Loom's `exec_write`/process-control tools still use generic tool-effect
   approval.
3. **Network approval service** — retry planning accepts a structured network
   approval context and preserves Codex's fresh-review rule, but the network
   service is owned by the MCP/network window.
4. **Product review response surface** — Loom app-server/UI still exposes boolean
   approve/deny. Core can represent and test `ApprovedForSession`, but product
   plumbing is outside this window.
5. **Review timeout/abort terminal semantics** — represented in the core enum but
   deliberately rejected by the legacy boolean resume API instead of being
   coerced to denial.
6. **Remote environment identity** — the current Loom runtime has one local
   execution environment, so structured approval actions use `environment_id =
   "local"`. The cache-key type already includes environment id for a future
   multi-environment runtime.
7. **Shell-command canonicalization breadth** — Codex delegates approval-key
   canonicalization to `codex-shell-command`, including richer shell parsing.
   Loom ports the observable simple Bourne-shell and PowerShell cases with a
   conservative Python parser and preserves ambiguous scripts verbatim. This is
   intentionally a safe subset rather than a claim of parser-complete parity.
8. **Linux permission-profile enforcement shape** — Loom's current Bubblewrap
   baseline exposes the host filesystem read-only and does not isolate network
   access. Therefore an additional read grant is already satisfied by ambient
   read visibility, and `network.enabled=true` cannot widen an already-open Linux
   network boundary. Scoped extra write paths are enforced with explicit bind
   mounts. Windows MXC can enforce the read/write/network subset directly. Full
   Codex permission-profile/network-proxy parity on Linux remains future work.
