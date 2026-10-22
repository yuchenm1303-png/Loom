# Development feedback loop

Loom's production TurnRunner owns a single loop for code edits, managed commands,
browser interactions and desktop actions. The current conversation model receives
the observations and chooses the next action. This change adds durable, repeatable
command checks, current-source freshness, general workspace image inspection and
browser sequence evidence to that loop.

## Running and repeating a check

Use the existing `exec` tool, including its ordinary permissions and sandbox:

```json
{
  "argv": ["npm", "run", "test"],
  "cwd": "frontend",
  "verification": {
    "case_id": "login-validation",
    "expectation": "Login validation regression tests exit successfully",
    "subject_paths": ["frontend/src/login", "frontend/tests/login.test.ts"],
    "expected_exit_code": 0
  }
}
```

Choose a direct executable appropriate to the host. `subject_paths` are always
workspace-relative, even when `cwd` is a subdirectory. Select the code and tests
whose changes invalidate the check, including relevant configuration. Do not watch
generated output or an entire repository containing dependencies and Git data.
There is no inferred dependency graph: omitted inputs are not freshness-tracked.

Loom hashes the scope immediately before and after executing the command. It
records the actual return code and the executed call ID in `CHECK_RECORDED`.
`verification` requires `wait=true` and `pty=false`; start the development server
separately using an ordinary background `exec`. These fields are included in the
pending action's identity, so a changed check scope cannot be substituted while an
approval is pending. They do not bypass command approval or sandbox execution.

A matching exit code produces `passed` only when the source snapshots match.
A different exit code produces `failed`. A matching exit code with changed source
produces `stale`. A command that cannot start, fails at runtime or times out is
`blocked`. These are exit-code assessments, not visual or business correctness
judgments. Boundary snapshots cannot detect source changes that revert completely
while a command is running. Scope is bounded to 32 paths, 1024 files and 32 MiB;
too broad a scope is rejected before starting the command (directory traversal is
also capped at 4096 entries). Checks with identical scopes share a fresh snapshot
within a single context projection, without caching content across model steps.

After a fix, repeat the relevant command using the same `case_id`. New results
supersede the earlier assessment for that case without deleting its history.
`loom_verification_state` gives the model the latest checks for the current turn.
Further source edits make old assessments stale. File additions, deletions and
same-length content changes are detected; returning to the identical tested bytes
restores freshness. No wall-clock timestamp is used as proof of freshness.

## Looking at actual results

`view_images` is directly available to the model. It reads 1–8 workspace images,
with optional labels and pixel crops:

```json
{
  "images": [
    {"path": "artifacts/contact-sheet.png", "label": "Sidebar closing"},
    {"path": "artifacts/frame-120.png", "crop": {"x": 0, "y": 0, "width": 800, "height": 200}}
  ]
}
```

It supports screenshots and figures from project scripts as well as Loom-owned
browser captures. Source hashes, original dimensions, crop geometry and attached
dimensions identify each observation. Image bytes attach only to the next model
request as untrusted tool observations; they do not enter session JSON or event
history. The existing context composer preserves the immutable Host-local request
prefix during retries and substitutes an explicit unavailable observation after a
Host restart. Workspace confinement applies to resolved symlinks too.

Images require Pillow and an image-capable conversation model. Individual source
files are limited to 16 MiB and 40 million pixels. Copies are resized to fit
2000×1600, with at most eight images and 8 MiB of encoded attachments per sampling
step. Use pixel crops or separate calls when small details matter. The transcript
provides image previews for both image inspection and sequence contact sheets.

## Capturing a browser sequence

Discover `browser_capture_sequence` with `tool_search`. For example:

```json
{
  "browser_id": "<live browser id>",
  "state_revision": 7,
  "trigger": {"type": "click", "index": 3},
  "frames": 8,
  "interval_ms": 100
}
```

The tool captures a baseline, optionally performs one revision-bound indexed
click or key press, and captures the remaining viewport samples. It holds a
browser operation lease across the entire sequence to prevent idle reclamation.
A stale trigger returns `not_executed` before taking a screenshot or injecting
input. Browser permissions, ownership, URL validation and cancellation still
apply. Sampling is bounded to 2–16 frames at requested intervals of 40–1000 ms and
32 MiB of original frame bytes.

The workspace receives original PNGs, a contact sheet and `manifest.json` with
actual capture start/end times and trigger start/end times. The current model
receives the sheet automatically. It can inspect original frames or crops using
`view_images`. The final DOM is refreshed after capture. Failed captures return a
partial manifest and whether the trigger was attempted; the model must inspect
current state before retrying an action. Cancellation can leave partial files,
but does not produce a successful capture result.

This is sampled evidence, not compositor-frame recording. Screenshot and action
latency can leave gaps and miss short transitions. For finer-grained animation analysis,
use a project's CDP screencast script (Loom's own Motion lab already provides
`screencast.mjs` and `contact.mjs`), check its timestamps and gaps, then inspect
its artifacts with `view_images`. Screencasts can also drop frames under load.
The tool does not label requested sampling intervals as actual presentation times.

## Visual and functional acceptance

After exercising and observing the changed behavior, record an explicit
`record_check` with an expectation, observation, verdict, executed evidence refs
and optional `subject_paths`. The source snapshot is taken when the model records
that assessment, not retroactively at the referenced action. This is deliberately
marked `assistant_assessment`; an executed call, image hash or pixel difference
does not prove the assistant's functional verdict. Existing unscoped records stay
readable with `freshness=untracked`. `read_check_ledger` computes current freshness
without rewriting historical verdicts.

Turn completion includes a separate `verification` summary. It covers declared
checks for that turn only, distinguishes failed/stale/blocked/incomplete from
passed, and does not silently equate finishing a turn with passing all requested
acceptance criteria. More than 24 distinct cases produce a bounded preview and
`incomplete` rather than concealing omitted failures. There is no keyword-based
completion judge, second model reviewer or forced retry loop. The current model
uses the feedback to fix and retest, or explains an actual blocker to the user.

## Validation

`tests/test_development_feedback.py` executes real subprocesses through the
production runtime with a scripted model: failing check → source fix → passing
check → another edit → stale result. It also checks source changes during a check,
explicit nonzero expectations, unstartable commands, persisted freshness, image
transport, crops and confinement. `tests/test_browser_capture_sequence.py` checks
revision guards, timing, artifacts, partial failure and cancellation.

`tests/test_browser_capture_live.py` is an opt-in real isolated Chromium test. Set
`LOOM_BROWSER_CAPTURE_LIVE=1`, `LOOM_NODE_PATH`, `LOOM_PLAYWRIGHT_MODULE` and
`LOOM_CHROMIUM_PATH`. It executes a real page click, validates actual before/after
pixels and DOM, captures a contact sheet and reads back original frames. It never
attaches to a user's existing tabs. These regressions establish harness behavior;
they do not measure a live model's independent reasoning quality or task success
rate.
