# Development feedback loop

Loom's production TurnRunner owns a single loop for code edits, managed commands,
browser interactions and desktop actions. The current conversation model receives
the observations and chooses the next action. This change adds durable, repeatable
command checks, current-source freshness, general workspace image inspection and
browser sequence evidence to that loop.

## Automatic acceptance obligations

Source edits now create a durable `verification_updated` state even if the model
never registers a check. UI sources (including component code, styles, templates
and theme/localization data) require visual acceptance; other recognized source
files require functional acceptance. Tests, docs, dependencies, generated build
directories and Loom scratch files do not create these obligations on their own.
The classification is conservative, not a claim to understand every project's
dependency graph. Type-only changes can be explicitly marked not applicable.
Declare command builds/type checks with `verification.check_kind="static"` and
behavior tests with `"functional"` (the default). Static results do not discharge
functional or visual obligations; the host does not semantically infer a command's
purpose from its shell text.

Tracked tool edits are observed through the turn diff. After commands, Loom also
compares dirty-source content and Git HEAD changes with its prior observation.
This catches scripts editing already-dirty files and a command that edits and
commits before returning. Nested Git workspaces stay confined to their subtree.
Non-Git projects use a bounded source walk. Discovery reports unavailable or
scope-too-large rather than claiming complete coverage. It is bounded to 128 dirty
source paths/512 KiB of Git status output, or 4096 entries/128 source files in a
non-Git tree, in addition to the content snapshot bounds below. Shared-workspace
changes are observed state, not proof which process authored them.

`loom_development_verification` is added to model context as soon as observed
changes need acceptance. A compiler/build check cannot discharge a visual
obligation. A visual `record_check(check_kind="visual")` pass needs references to
an executed interaction and fresh visual evidence from the current source
generation. The image must have reached a model request with a committed response;
merely creating a PNG or assembling a request that was rejected is insufficient.
Reading a workspace image older than the changed sources is not fresh evidence.
File times for project-generated images are a heuristic, not authenticated
capture provenance; Loom-owned browser/desktop captures have stronger linkage.
The check verdict remains the model's assessment, not proof it interpreted pixels
correctly. External source changes before recording are observed again so an old
capture cannot be promoted to a new-source pass by snapshotting at record time.

State and recovery counters survive context compaction, approval waits and safe
handoffs. Pending changes remain visible in follow-ups. Enforcement applies to
changes made in the current turn: a later user who only asks for status is not
forced into continued GUI execution.
Their terminal response still carries a runtime notice if prior acceptance is
pending, so a follow-up cannot silently turn an unverified change into success.

When the model tries to finish with unresolved current-turn obligations, the
response is retained as commentary and a structured
`verification_finish_requested` event requests continuation in the same model/tool
loop. No final-answer keywords or second judge model are involved. Two recovery
requests are allowed for the same source generation (six total per turn if source
keeps changing); another unverified ending stops with `limit_reached` and an
explicit verification-incomplete result. Cancellation and normal resource cleanup
still apply. This bounds failed convergence rather than looping forever or
accepting an unsupported success claim.

For an actual environment blocker, or a change with no runtime behavior, use
`resolve_verification` with source paths, `blocked`/`not_applicable`, a concrete
reason and evidence references. Neither disposition is a pass. A blocked terminal
result receives a runtime verification notice in both the saved result and the
rendered transcript, separate from model prose. Subsequent edits invalidate the
disposition too.

## Project recipes and replayable browser acceptance

Project owners can supply `loom.verification.json`, or override it locally with
`.loom/verification.json`. Version 1 accepts up to 64 features containing `id`,
`sources` (workspace-relative glob patterns), `entry`, `setup`, `exercise`, and
`expectation`. Matched recipes are bounded advisory context, never authority to
run commands or change permissions. Invalid maps are reported in state. Unmapped
source still creates obligations; it is not silently exempted. The repository
ships initial sidebar and composer recipes against its existing renderer fixture;
renderer coverage must not be presented as installed Electron coverage.

Discover `browser_run_check` with `tool_search`. Start the app using existing
managed processes, open the intended target and inspect its current state first.
Then submit the live `browser_id`, initial `state_revision`, `verification`
(`case_id`, `expectation`, `subject_paths`) and 2–16 steps:

```json
{
  "browser_id": "<live id>",
  "state_revision": 7,
  "verification": {
    "case_id": "panel-toggle",
    "expectation": "Closing leaves the toggle visible",
    "subject_paths": ["src/Panel.tsx"]
  },
  "steps": [
    {"type": "action", "tool": "browser_click", "arguments": {"index": 3}},
    {"type": "assert", "label": "Toggle remains visible",
     "expression": "document.querySelector('[data-toggle]')?.getBoundingClientRect().width > 0"}
  ]
}
```

Actions reuse native browser tools (navigate, click, coordinate click, type, press,
hover, scroll, select and wait). All steps are schema-validated before input; each
gets the actual current revision. Recipes cannot substitute another browser ID,
inject revisions, run arbitrary tools or bypass central account/permission/URL
checks. Parent approval covers the declared recipe, and children still cross the
orchestrator. Known indexes are appropriate only for a known page/fixture. Assert
target identity before indexed actions; use ordinary adaptive browser tools when
navigation or DOM changes make a recipe non-replayable.

Assertions evaluate in the page and must return boolean `true`, not a truthy
string/object/number. The runner stops at the first failure, records nested
execution IDs, captures a result screenshot, compares source snapshots and records
a scoped check. An interaction and at least one assertion are required. The image
is attached on failure too and remains previewable in the transcript. A passing
automated recipe discharges a visual obligation only after its image reaches a
committed model step. Page-script assertions have the same authority/limitations
as `browser_eval`; their expected behavior is not inferred by the host.

Reusing a case ID with a different command/expectation or browser recipe marks its
definition changed and the result `needs_review`, removing automatic acceptance
credit. An intentional change requires an explicit assessment with current
evidence. This prevents silently changing an expected exit code or assertion to
turn a failure into automatic success; it does not establish the correctness of
the human/model-chosen acceptance standard.

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
`incomplete` rather than concealing omitted failures. Automatic source obligations
are reported separately as `development_verification`; they drive the bounded
same-model continuation described above. A pass covers declared assertions and
observed sources, not all possible user behavior or an unverified deployed build.

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

`tests/test_automatic_verification.py` adds adversarial completion, no check
registration, compiler-only UI claims, stale/unseen images, shell edits, committed
edits, nested workspaces, persisted follow-up state, blockers, changed acceptance
definitions and cancellation. `tests/test_browser_verification.py` exercises
strict assertions, native actions, source invalidation and policy boundaries.
The dedicated `Development verification loop` GitHub Actions workflow runs the
real Chromium regression on pushes and pull requests without any model key.
Visual-feedback transcript contracts run with the existing frontend CI checks.

The real Chromium test also supports an explicitly enabled live-model evaluation:
set `LOOM_VERIFICATION_LIVE_MODEL=1`, `LOOM_VERIFICATION_MODEL_KEY`,
`LOOM_VERIFICATION_MODEL_URL`, `LOOM_VERIFICATION_MODEL_NAME`, and optionally
`LOOM_VERIFICATION_MODEL_PROVIDER` (default `openai-compatible`). Credentials stay
in environment/RAM. It gives the real model a clipped local button and a normal
repair request without prescribing tools, caps the run at 18 model steps/30 tool
calls, records a report and independently checks the final page with Chromium.
It requires both the model's visual acceptance and an independent hit-test of the
button bottom. It is opt-in because it consumes provider usage. Without an
available credential this evaluation has not run; deterministic providers and
real browser tests must not be reported as real-model autonomy measurements.

Open-source design references: OpenHands' real-app verification/feature-map
workflow and Playwright's replay/trace/testing facilities informed the structure.
No upstream agent loop or source code was copied. Loom retains its own runtime,
permissions, context transport, cancellation and event history.
