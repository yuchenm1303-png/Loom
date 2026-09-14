# Codex source pin for approval/sandbox parity

Audited upstream: `openai/codex@36f0dbe796d9bb1a18a0fc0640ed08b3e1d54564`.

The approval/sandbox source files used for this port are:

- `codex-rs/core/src/tools/orchestrator.rs`
- `codex-rs/core/src/tools/sandboxing.rs`
- `codex-rs/core/src/tools/approvals.rs`
- `codex-rs/core/src/tools/runtimes/unified_exec.rs`
- `codex-rs/core/src/tools/runtimes/apply_patch.rs`
- `codex-rs/core/src/unified_exec/stdin_approval.rs`
- `codex-rs/core/src/command_canonicalization.rs`
- `codex-rs/sandboxing/src/policy_transforms.rs`
- `codex-rs/sandboxing/src/denial.rs`
- `codex-rs/core/tests/suite/approvals.rs`

The previous audited parent `1715e55076737158ba61d43158ede504de6d4ce1`
and this pin differ only in unrelated Windows desktop uninstall/provisioning code;
none of the files above changed in that one-commit range.
