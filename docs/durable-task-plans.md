# Durable task plans

Loom has two planning modes in the existing session event journal. `update_plan`
maintains lightweight milestones for ordinary multi-stage work. Set `mode: graph`
for long-running work that needs dependencies, claimed work packets and review
gates. Both modes have a stable `plan_id`, monotonically increasing `revision`,
and an explicit lifecycle independent of the current model turn.

An active plan is projected into subsequent turns, including after a restart.
It is assistant-maintained task state, not authorization to keep pursuing an old
request after the user changes direction. Replace a plan with `update_plan`
(`replace: true`, current `plan_id`, `expected_revision`, and `explanation`), or
archive it with `manage_plan`. Prior definitions, reports and assessments remain
in the append-only journal. Old journals receive deterministic identities when
read; no destructive migration is required.

## Creating a graph

Call `update_plan` with an initial definition such as:

```json
{
  "mode": "graph",
  "plan": [
    {
      "id": "build",
      "step": "Implement the contract",
      "status": "pending",
      "instructions": "Implement the requested API and verify its live caller.",
      "acceptance": ["The real caller exercises the new contract"],
      "deps": []
    },
    {
      "id": "review",
      "step": "Review the contract",
      "type": "review",
      "status": "pending",
      "instructions": "Judge the contract and failure handling. Report findings; do not fix them here.",
      "acceptance": ["The implementation and its failure handling satisfy the request"],
      "deps": ["build"],
      "max_review_cycles": 3
    },
    {
      "id": "deliver",
      "step": "Deliver verified results",
      "status": "pending",
      "instructions": "Prepare the requested deliverable after review passes.",
      "acceptance": ["The deliverable includes verified results and remaining limitations"],
      "deps": ["review"]
    }
  ]
}
```

Graph IDs are unique, dependencies must exist, and cycles are rejected before
anything is written. Reviews depend on the implementation steps they judge.
All steps start pending without fabricated results. Definitions are immutable
within a graph identity: changes in scope require explicit replacement.

## Execution protocol

1. `manage_plan(action: inspect)` returns the current identity, revision,
   summary, ready IDs and any claimed packet.
2. Mutations include that `plan_id`, `expected_revision`, and a unique
   `operation_id`. `claim` chooses ready work in definition order, prioritizing
   open feedback. An optional `step_id` chooses a specific ready step. Exactly
   one item can be claimed per plan.
3. The claim returns a durable `claim_id` and a packet containing instructions,
   acceptance criteria and upstream reports. Repeating claim returns the same
   outstanding work. Claims survive turns and restarts; they are coordination
   records, not resumed process handles or execution permission.
4. Implementation `submit` requires matching `step_id` and `claim_id`, a report,
   `evidence_refs`, and an honest `outcome`. Only `completed` **and** `passed`
   dependencies unlock downstream work. An implementation failure remains a
   valid recorded execution result without unlocking its dependents.
5. `review` requires a report, evidence and `verdict: passed | needs_changes`.
   Rejection creates feedback; the next claim is rework. `resolve` requires a
   rework report backed by new evidence, after which the review can be claimed
   again. Every review attempt is counted, including the passing attempt.
6. Exhausting `max_review_cycles` blocks the review and its dependents. It never
   passes automatically. Explicitly `reopen` the upstream implementation with an
   explanation to start another implementation round; all transitive dependent
   assessments and review cycles are invalidated. History is retained.

`block` and `unblock` require an explanation. Blocking the outstanding item
releases its claim. An exhausted review cannot be unblocked to bypass its limit.
`archive` also requires an explanation; archiving unfinished work does not claim
it succeeded. Reports and feedback are stored inside durable revision events,
so later submissions cannot overwrite earlier reports.

## Retry and evidence guarantees

Graph mutations reject stale revisions. Retry an identical operation using its
original `operation_id`; a committed operation returns the **current** state
without executing twice, even if the process died before its tool receipt was
written. Reusing the ID for different arguments is rejected. Runtime session
execution leases serialize execution across processes; non-parallel planning
tools execute within that boundary. Lightweight callers can still use the old
`update_plan` shape, while supplying version fields enables stale-write checks.

Tool evidence must refer to an executed result in this session, not an invented
ID or a rejected action. File evidence must resolve inside the workspace and is
stream-hashed with SHA-256. Evidence facts are saved with the assessment; updating
another step preserves them even if the original file was subsequently changed
or deleted. A hash identifies observed bytes; it is not a backup of those bytes.
Use durable report text and tool results for historical reconstruction.

Changing an assessment requires an explanation. Promoting a non-passing result,
or passing reopened work, requires a new evidence identity/version. Reopening
cannot launder an old failure into a pass using unchanged evidence. New evidence
does not by itself prove the assistant's verdict: acceptance remains an explicit
assistant assessment, and a review gate does not implicitly launch or require an
independent model.

## Runtime and presentation

The runtime records plan counts and acceptance status when the turn ends. A turn
can legitimately end with incomplete work (for example, a request for input).
Such a turn does not mark the task passed. The model's `mark_thread_goal(complete)`
is rejected while an active plan has pending, blocked or unpassed work.

The App Server emits committed plan events as `task_plan` items. Live and reloaded
views therefore retain truth even when the corresponding tool completion is
missing. The progress panel remains inspectable between turns, distinguishes
execution from acceptance, shows dependencies, review attempts, feedback and
file versions, and disappears on explicit archive. Older hosts can still use
successful planning-tool receipts as a presentation fallback.

## Validation

- `tests/test_durable_plan_graph.py`: graph validation, cross-turn/restart
  recovery, claim ownership, rework, exhausted reviews, invalidation, stale
  revisions, retry recovery, immutable evidence facts and goal completion.
- Existing plan/evidence, commit-journal, recovery, runtime and App Server tests.
- `desktop-react/scripts/tests/live-task-progress.test.mjs` and
  `plan-update-notes.test.mjs`: state selection and stage notes.
- `desktop-react/scripts/tests/task-progress-browser.mjs`: real Transcript,
  wide/narrow layouts, retained plans, review blockers, evidence and archive.
