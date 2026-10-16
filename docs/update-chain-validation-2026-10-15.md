# Update chain validation — 2026-10-15

## Scope and result

The local Windows installation of 0.1.91 was verified to start, restore the
existing conversation, display the right tool dock, and complete a manual update
check with an up-to-date Host. Subsequent hardening described below is committed
source validation, not a claim that the installed application already contains it.

This audit used an isolated source snapshot so concurrent model/UI work did not
alter the tests. The updater, application updater and their regression tests
were compared by SHA256 with the workspace after validation and matched.

## Verified behavior

| Case | Evidence |
| --- | --- |
| Slow account verification and concurrent requests | `updateEligibility.test.mjs`: a lookup longer than the former 3-second limit succeeds; concurrent callers share work; later revocation is checked again |
| Credential readiness and failed candidate recovery | `applicationLoader.test.mjs`: waits for readiness, preserves the prior active build, quarantines failed/corrupt candidates |
| Cold packaged startup with a pending update | Real `host-process-smoke.mjs --packaged --pending-update` passed during the local installation task; now part of `package:win` |
| Real staged application activation | `application-update-smoke.mjs --asar` passed: relocated modules, installed dependencies, sandboxed preload and health activation |
| Concurrent checks and installer priority | `updaterEligibilityRetry.test.mjs`: one installer check, followed by component checks only when no installer is pending; component restarts are blocked during installer work |
| Active tasks and missing restart guard | The same test blocks automatic restart before guard registration and manual restart while work is active; task state is rechecked after account verification |
| Repeated install actions | Concurrent install actions schedule exactly one restart |
| Verification outage after a download | Verified installer/application downloads are retained, cannot activate without fresh permission, and remain available after verification recovers |
| Download integrity, failure recovery and preferences | Electron suite covers checksums, unsafe manifests, cache repair, interrupted staging, rollback, account revocation, and automatic/manual preference behavior |
| User-visible state | Software status tests distinguish idle, automatic updates off, actual Host work, account verification failure, unsupported builds, and task-blocked installation |

Final relevant-source checks:

- TypeScript typecheck and Electron compilation passed.
- All 170 Electron tests passed after the final restart guard changes.
- Four software update status tests and the release compatibility test passed.
- Four smoke watchdog/process-error tests passed earlier in the audit; the helper
  was unchanged afterward.
- Real staged application/ASAR activation passed after correcting its readiness
  probe to avoid top-level await.

The Windows installer upgrade/process cleanup smoke tests and real installation
were also passed in the preceding local installation task. They are not a
production rollout to other users.

## Release boundary still outstanding

At the publication check in this audit, GitHub's latest public installer was
0.1.90. This task did not publish a GitHub Release or run the production release
workflow. A local 0.1.91 installation is not evidence that other users received
the repair or this audit's subsequent hardening.

Before calling the rollout complete:

1. Build and publish a fresh immutable Windows release from the final intended
   source, passing the packaged startup, pending-update startup, executable and
   installer gates.
2. Verify the published installer, blockmap and `latest.yml` identify the same
   release; publish the component channel for that exact bootstrap baseline.
3. Upgrade an installed 0.1.90 through the published automatic channel, verify
   an idle restart, conversation restoration and visible tool dock.
4. Exercise a 0.1.90 client whose account lookup exceeds its old short deadline.
   If that client cannot fetch the repair, provide a one-time manual installer;
   a newer client cannot retroactively change the older installed checker.

No zero-failure guarantee is made for future network/service failures. The
verified requirement is that failed verification cannot authorize activation,
does not erase a verified download, and leaves a usable recovery/retry path.
