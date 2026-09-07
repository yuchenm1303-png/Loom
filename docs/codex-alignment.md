# Loom ↔ OpenAI Codex runtime alignment

This document is an implementation checklist, not a claim that Loom is a Codex fork.
Loom keeps its Python runtime, App Server, PySide desktop, Browser Use, and Computer Use adaptations while using Codex as the primary behavioral reference for mature Agent-runtime boundaries.

## Pinned baselines

- Loom baseline for phase 3: `2e25b2564f3eaa0841ee28f90293ebcc99313f94`
- OpenAI Codex baseline for phase 3: `0df39752cbc4b88d0194ec62bdb0d56fbda4b014`
- Microsoft MXC stable config contract reviewed for the Windows backend: `0.8.0-alpha`

Previous Codex baselines were `694b6319d3ad2399f6e435760a22d9b9357f0697` and `5ecb3afd1bf405149e2159bfda50093b0c1b5fab`.

Re-audit against newer Codex commits before each large alignment phase instead of assuming this document remains current forever.

## Source boundaries reviewed

Codex source reviewed for the current baseline:

- `codex-rs/core/src/config/resolved_permission_profile.rs`
  - constrained `PermissionProfileState`
  - resolved permission-profile snapshots
- `codex-rs/core/src/session/step_context.rs`
  - immutable request-scoped `StepContext`
  - finalized `ToolRouter` advertised and executed for the exact sampling request
- `codex-rs/core/src/session/step_settings.rs`
  - selected settings separated from immutable resolved step settings
- `codex-rs/core/src/tools/spec_plan.rs`
  - canonical per-step tool-plan construction and exposure
- `codex-rs/core/src/tools/handlers/shell_spec.rs`
  - shell/exec semantics are expressed in the actual tool specification
  - approval-related command options are part of the tool/runtime protocol rather than a model-side capability matrix
- `codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs`
  - exec derives approval, environment, sandbox context, and additional permissions from frozen step/turn state
- `codex-rs/sandboxing/src/spawn.rs`
  - sandboxing is an executor-native spawn concern
  - Windows launch receives the resolved permission profile, workspace roots, exact environment, TTY state, and filesystem overrides at the spawn boundary
- `codex-rs/sandboxing/src/windows.rs`
  - Windows backends fail closed when a requested filesystem policy cannot be represented/enforced
  - read/write roots and deny carveouts are explicit runtime policy, not model instructions
- `codex-rs/windows-sandbox-rs/src/unified_exec/mod.rs`
  - Windows sandbox launch is part of the same unified exec lifecycle, including PTY/stdin state
  - elevated and unelevated backends are selected behind one session request shape
- `codex-rs/core/src/windows_sandbox.rs`
  - host setup/provisioning is explicit and separate from normal command execution
  - setup capability is not silently inferred or elevated during an ordinary command

Loom does not copy Codex's Windows restricted-token/ACL implementation mechanically. Loom's Windows backend is adapted to Microsoft MXC while preserving the same runtime invariants: containment at spawn, policy from resolved runtime state, fail-closed required mode, explicit host preparation, and no model-side sandbox guessing.

## Alignment matrix

| Area | Loom status | Codex-alignment direction | Priority |
| --- | --- | --- | --- |
| StepContext | Stronger after phase 1 | Keep one immutable per-sampling snapshot for effective runtime state | P0 |
| Permission profile | Stronger after phase 1 | One resolved permission snapshot is the source for authorization and containment | P0 |
| Tool authorization | Stronger after phase 2 | Model chooses from finalized tool specs; runtime alone owns allow / approval / deny | P0 |
| Sandbox policy | Stronger after phases 1/3 | Derive containment from resolved permission snapshot; executor owns enforcement | P0 |
| Windows OS sandbox | Implemented via optional MXC backend in phase 3 | Keep real enforcement tests and never claim availability from launcher presence alone | P0 |
| Unified exec + PTY | Strong | Continue using one managed lifecycle; keep sandbox planning immediately before spawn | P1 |
| Shell environment | Partial | Move from secret filtering alone toward an explicit resolved shell-environment policy | P1 |
| Tool planning / exposure | Partial-strong | Keep exact step-specific router; continue aligning direct/deferred/code-mode planning | P1 |
| Tool search | Strong | Preserve turn-scoped deferred activation and restart-safe approval reconstruction | P1 |
| Session / thread state | Partial | Make App Server/runtime authoritative for resume/fork metadata and effective settings | P1 |
| Context compaction | Partial | Align durable history/compaction boundaries and avoid reconstructing effective settings from summaries | P2 |
| MCP | Partial-strong | Continue dynamic binding, verification, and per-step exposure alignment | P2 |
| Skills / instructions | Partial-strong | Align instruction discovery/lifetime semantics where useful; preserve Loom Python layout | P2 |
| Browser Use | Loom extension | Keep Loom adapter boundary; use common runtime permissions/state | Loom-specific |
| Computer Use | Loom extension | Keep Alibaba/UI-TARS adapters outside core runtime; use common runtime permissions/state | Loom-specific |
| Native desktop | Loom extension | Keep PySide/App Server separation; do not copy TUI-specific architecture mechanically | Loom-specific |

## Phase 1: resolved permission snapshot

Implemented on `align/codex-permission-snapshot-v1` and merged as `4b9e97176f4033df26561a8d0219f2eac028eac2`:

1. Add immutable `PermissionSnapshot` as the canonical resolved permission version.
2. Keep tool authorization and filesystem containment as explicit dimensions of that same snapshot.
3. Capture the exact snapshot in `StepContext`.
4. Make `ToolOrchestrator` authorize from the captured snapshot.
5. Make `SandboxManager` derive `SandboxMode` from snapshot `FileSystemAccess` instead of independently switching on `PermissionMode`.
6. Preserve legacy `permission_preset()` / `permission_mode=` entry points by routing them through the canonical snapshot resolver.
7. Preserve existing behavior, including the compatibility `approval` mode: read-only tools are auto-allowed, mutating/sensitive tools require approval, and approved process execution remains workspace-contained.

This phase intentionally did **not** claim Windows OS sandbox completion.

## Phase 2: tool-first model behavior

Implemented on `align/codex-tool-first-behavior-v2` and merged as `2e25b2564f3eaa0841ee28f90293ebcc99313f94`:

1. Keep the finalized per-step `ToolRouter` definitions as the model-visible capability surface.
2. Stop injecting a dynamic allow/approval/deny capability matrix, permission mode, filesystem mode, sandbox state, individual tool names, or hand-written intent routes into the system prompt.
3. Keep only a small invariant harness contract: select suitable tools from their schemas, call them directly, and let Loom Runtime own allow / approval / deny.
4. Keep `PermissionEngine` and `PermissionSnapshot` authoritative for actual execution. No permission is weakened and the model is not asked to predict authorization before calling a tool.
5. Treat tool status/failure as subsystem-scoped evidence. One disabled tool must not implicitly disable unrelated shell, filesystem, browser, MCP, or GUI capabilities.
6. Preserve deferred discovery: if `tool_search` is exposed and direct tools are insufficient, use it before claiming that no capability exists.
7. Add regression coverage proving the model prompt is permission-invariant while Workspace approval and Read Only denial still happen in Runtime after the model emits the tool call.

The key behavioral invariant is:

`user intent -> finalized tool specs -> model tool call -> runtime authorization -> allow / approval / deny -> result`

not:

`user intent -> model reads a permission matrix -> model guesses whether it is allowed -> maybe asks user in prose -> tool call`.

## Phase 3: Windows executor-native sandbox

Implemented on `align/windows-mxc-sandbox-v2`:

1. Add `windows-mxc` as a real `SandboxBackend` using Microsoft MXC `wxc-exec.exe` and stable schema `0.8.0-alpha`.
2. Discover MXC from `LOOM_WINDOWS_SANDBOX_EXECUTABLE` or PATH, but do **not** mark it usable from file presence or `--version` alone.
3. Run MXC's read-only `--probe` and require a recognized isolation tier (`base-container`, `appcontainer-bfs`, or `appcontainer-dacl`) before `SandboxSnapshot.enforced` can become true.
4. Preserve `AUTO` honest fallback and `REQUIRED` fail-closed behavior. Full/unrestricted access intentionally bypasses the OS sandbox.
5. Build the sanitized child environment before sandbox planning, then pass the exact same environment to both MXC policy construction and process spawn.
6. Map resolved filesystem access to MXC policy:
   - workspace-write: workspace read/write
   - read-only: workspace read-only
   - control-plane metadata (`.git`, `.loom`, `.agents`) remains read-only when present
7. Deny outbound network, inbound network, host loopback, clipboard, UI access, and input injection in the Windows sandbox policy.
8. Keep normal command execution non-elevating. MXC host preparation remains an explicit operator/setup concern when a fallback isolation tier requires it.
9. Add a dedicated `windows-sandbox-smoke` CI job that installs the official `@microsoft/mxc-sdk`, probes the actual Windows runner, and runs real commands through `ProcessStore`.
10. Real enforcement tests assert that allowed workspace writes succeed, outside writes fail, read-only workspace writes fail, metadata carveouts stay read-only, secret-like environment variables are absent, and network access is blocked.

This phase does **not** claim MXC is bundled with Loom. Runtime status stays truthful: on a normal Windows installation without a usable `wxc-exec.exe`, the backend is unavailable.

## Next phases

1. Introduce a resolved shell-environment policy consumed by Unified Exec instead of relying only on secret-name filtering and a backend-local allowlist.
2. Consolidate session/turn/step effective settings so resume/fork cannot silently rebuild different runtime semantics.
3. Review tool-spec planning against current Codex `spec_plan` and reduce remaining ad-hoc exposure logic.
4. Re-audit context compaction, MCP verification, skills/instructions, and multi-agent handoff semantics.

## Rule for future Loom core work

When Codex already has a mature implementation for a core Agent-runtime concern, inspect the current Codex source first and preserve its behavioral invariants where they fit Loom. Adapt language/platform details to Python/Windows/PySide rather than copying Rust structure mechanically. Loom-specific Browser Use and Computer Use capabilities remain extensions behind stable runtime interfaces.
