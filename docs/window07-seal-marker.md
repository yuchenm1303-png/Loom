# Window07 — SEALED

Window: Codex parity tests / CI / hardening

Branch: `codex-parity-tests-hardening-v1`

Final audited upstream baseline: `openai/codex@36f0dbe796d9bb1a18a0fc0640ed08b3e1d54564`

Seal basis:

- 9 cross-module parity regression tests committed.
- 0 production runtime files changed by the parity test/hardening slice.
- P0/P1/P2 inventory and conservative 12-domain scorecard recorded.
- Direct-tool metadata, metadata-aware compaction, MCP catalog revision/prepared-call authority, and broader step/approval/network semantics remain explicitly Partial/Missing rather than being papered over.
- Stale Qt desktop CI references removed.
- Windows app-server/UTF-8 protocol coverage restored with `windows-app-server-smoke` at commit `efaeceae86c37ed8f663d52859e5cc9e1b5a0a44`.
- GitHub Actions recognizes the restored job, but current jobs still terminate pre-runner with no executed steps/logs.

Validation state at seal:

`contract committed, CI not executed`

This seal is a contract/audit freeze, not a claim that CI is green. PR #126 remains Draft and the documented merge holds remain in force until actual executable CI evidence exists.
