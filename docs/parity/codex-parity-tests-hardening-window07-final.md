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

However, deleting the entire historical `windows-desktop-smoke` job also removed two still-current Windows protocol regressions:

- `tests/test_app_server_client.py`
- `tests/test_app_server_stdio_encoding.py`

The second test explicitly protects UTF-8 JSON-RPC behavior under a non-UTF-8 Windows-style Python stdio configuration. Those tests must retain a Windows CI surface.

The intended minimal replacement is a `windows-app-server-smoke` job that installs `.[dev]` and runs exactly those two test files. The obsolete Qt extra/tests/launcher must remain deleted.

The connected GitHub write surface used by the audit window rejects writes under `.github/workflows/`, including both replacement of `ci.yml` and creation of a separate workflow file. The auditor did not bypass that safety boundary through lower-level Git object APIs. Therefore this workflow correction is specified but not falsely recorded as applied.

## Validation state

The observed parity-suite CI state remains:

`contract committed, CI not executed`

Run `34758500254` created eight failed jobs with no executed steps/logs; the general `test` job likewise had `steps=[]` and no retrievable log blob. This is pre-runner/non-execution evidence, not a pytest result.

## Window07 seal status

The parity inventory, nine test contracts, conservative scorecard, merge holds, and CI diagnosis are accepted. The only remaining repository change is the narrow Windows app-server smoke workflow restoration above.

Until that workflow edit is actually committed, Window07 is **CONDITIONAL PASS / NOT SEALED**. Once the job is restored without reviving the obsolete Qt desktop surface, Window07 can be marked **SEALED** without further production-code or parity-test changes.
