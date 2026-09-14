# Window07 final audit addendum

This addendum supersedes the baseline and CI-cleanup wording in `codex-parity-tests-hardening-2026-09-13.md` where they conflict.

## Final upstream baseline

- Final audited Codex main: `openai/codex@36f0dbe796d9bb1a18a0fc0640ed08b3e1d54564`.
- Previous implementation baseline: `1715e55076737158ba61d43158ede504de6d4ce1` (`Bind direct tool-call metadata to invocation outputs (#45185)`).
- The single upstream commit between those SHAs is `Register Windows desktop uninstall ownership before sandbox setup (#45224)`.
- Its diff is Windows Desktop uninstall ownership / ACL / sandbox provisioning plumbing. It does not change the approval, Step activation, direct-tool metadata, MCP prepared-call, compaction, app-server reconnect, concurrency, or recovery contracts inventoried by Window07.
- Therefore the nine parity tests and P0/P1/P2 inventory remain valid; no test redesign is required from this upstream drift.

## Corrected CI ruling

The stale Qt desktop configuration was correctly identified from real runner logs: the repository has no `desktop` extra, no `loom-desktop` script, no `loom_desktop.py`, and the old Qt test files are gone.

Deleting the entire historical `windows-desktop-smoke` job also removed two still-current Windows protocol regressions:

- `tests/test_app_server_client.py`
- `tests/test_app_server_stdio_encoding.py`

That overbroad cleanup is now corrected. Commit `efaeceae86c37ed8f663d52859e5cc9e1b5a0a44` adds `windows-app-server-smoke`, installs `.[dev]`, and runs exactly those two test files. It does not restore the obsolete Qt extra, tests, launcher, or module.

The new workflow job is visible to GitHub Actions in run `34759906008`, confirming the YAML is accepted and the intended Windows app-server protocol coverage is structurally restored.

## Validation state

The observed parity-suite CI state remains:

`contract committed, CI not executed`

Run `34759906008` created nine jobs, including `windows-app-server-smoke`, but all nine completed with no executed steps/logs. Explicit step lookup for `windows-app-server-smoke` returns `steps=[]`. This remains pre-runner/non-execution evidence, not a pytest/npm result and not a test failure result.

## Window07 seal status

The parity inventory, nine test contracts, conservative scorecard, merge holds, CI diagnosis, upstream-baseline correction, and Windows app-server smoke restoration are accepted.

Window07 is **SEALED** at the contract/audit level. The seal does not claim green CI: the parity suite still has no executed CI result because the repository continues to exhibit a separate pre-runner scheduling/eligibility failure outside this window's implementation scope.

No further production-code or parity-test changes are required for Window07. PR #126 should remain Draft until the broader merge holds are cleared by actual executable CI evidence.
