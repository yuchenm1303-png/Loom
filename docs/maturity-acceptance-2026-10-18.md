# Desktop reliability acceptance — 2026-10-18

## Scope and conclusion

This acceptance checks the local development source, including existing workspace
changes. It does not certify the currently installed executable or real external
provider accounts. A passing unit suite is not evidence of a successful paid model
turn, browser pairing, OS interaction, OAuth flow, or installer upgrade.

## Repaired behavior

- Pending workspace reads display unknown state instead of claiming no repository,
  no changes, a clean tree, or no files.
- Passive repository, workspace, diff, directory, search, and text reads have a
  20-second UI deadline. Timeout leaves a visible error and permits retry. Late
  responses cannot apply after the timeout. Mutation requests keep their existing
  authoritative completion behavior; they are not automatically retried.
- Changing or closing the project panel invalidates old workspace, diff, staging,
  commit, and instruction-save results. Previous project actions cannot replace
  the currently displayed project's state.
- Repository reads report a missing Host bridge, release busy state after errors,
  ignore results from previous projects, and reset project-specific PR drafts.
- File failures no longer appear as empty directories, indefinite loading, or a
  successfully synchronized file.
- Archived project memories remain selected instead of being reset to the active
  view by a callback dependency change. Memory and instruction-file cards are
  remounted across project/open changes, and their passive reads are bounded.
- Git subprocesses cannot consume the app-server's standard input and do not
  create Windows console windows.

## Repeatable verification

- `npm run typecheck` in `desktop-react`.
- `node --test scripts/tests/*.test.mjs`: 258 passed, including 10 new project-read
  and component behavior regressions.
- `npm run test:electron`: 208 passed. Set `LOOM_TEST_PYTHON` to a working Python
  executable if `python` is absent from PATH.
- `npm run build:renderer`: passed; Vite still reports large bundle chunks.
- Project workspace/files/Git/project-RPC backend tests: passed.
- Core navigation, instructions, conversation, tool availability, diagnostics,
  and connector backend tests: passed.
- `node scripts/host-process-smoke.mjs` uses isolated data, a Chinese workspace
  path, a credential-free runtime, and real Electron/Host/Python IPC. It exercises
  project creation, workspace status, Git repository discovery, initialization,
  staging, committing, and file listing, then verifies that Host survives client
  exit and accepts another client.

The machine's normal Python launcher and existing virtual-environment launcher
could not start. Acceptance used the bundled Codex Python with the existing
`.venv/Lib/site-packages` on `PYTHONPATH`. Windows-specific collection additionally
needs `win32` and `win32/lib` on that path. This is an environment workaround,
not a product runtime fix or a certification of that virtual environment.

## Remaining release evidence

- Full backend run: 2,826 passed, 5 skipped, 3 failed. All three failures were
  test environment/isolation issues: the MCP stdio fixture did not forward an
  explicit dependency path, a UTF-8 snapshot assertion used the Windows default
  encoding, and a retry assertion patched global `time.sleep` and captured
  background-thread sleeps. These tests were repaired; all 14 tests in the three
  affected modules then passed. A second full backend run was not performed.
- The post-change project instructions/workspace/memory/language selection passed
  26 backend tests, and the Git module passed its full targeted suite including
  the new subprocess isolation test.
- Exercise real model send/stream/cancel/recovery with configured credentials.
- Exercise browser pairing and a real browser action, and Windows computer control
  with the installed driver and permissions.
- Exercise real connector authorization and expiration/reconnection.
- Build and install the release candidate, repeat the project workflow in that
  installed UI, and test upgrade/restart with active-task protection.
- Visually inspect the candidate at supported display scales. Existing unrelated
  typography changes were present during this source acceptance.

Until those real-environment checks pass, describe the project as undergoing
stabilization rather than fully production-ready.

Other tasks changed model/image/transcript code during acceptance. Those changes
are not included in this task's commit. The composer sign-in test harness was
updated for its new image-notice input; the sign-in behavior itself is unchanged.
